/**
 * Client-side collection for the research embed's optional behavioral-
 * tracking features (keystroke dynamics, temporal delays, tab-visibility
 * during streaming, clipboard copy/paste). See
 * backend/open_webui/routers/research_embed.py (POST /events,
 * GET /models/{model_id}/tracking-config) and
 * backend/open_webui/models/research_embed_events.py for the server side.
 *
 * Design notes:
 * - Tracking toggles are per-model (Model.meta.research_embed's track_*
 *   keys), not instance-wide -- setResearchEmbedTrackingModel() fetches and
 *   caches the current chat's model's toggles, and every record* function
 *   re-checks them before doing anything. The toggles are also re-checked
 *   server-side on ingest against that same model -- so flipping a toggle
 *   off in Workspace > Models > Research Embed stops new data immediately,
 *   it doesn't just hide a client-side control.
 * - When a toggle is off, nothing is buffered or measured for that event
 *   type at all (not just "collected but not sent") -- there's no reason to
 *   hold data in memory for a feature the admin hasn't turned on.
 * - Events are batched in memory and flushed periodically or on
 *   visibility/unload, rather than one HTTP request per keystroke. Every
 *   flush tags the whole batch with the CURRENT model id -- fine in
 *   practice since a research embed chat page has exactly one model for its
 *   whole lifetime.
 * - The unload-time flush uses navigator.sendBeacon rather than fetch,
 *   since a fetch started during page teardown is not reliably delivered.
 *   sendBeacon can't set an Authorization header, but Open WebUI also sets
 *   a same-origin "token" cookie on login (see auths.py's set_cookie calls)
 *   that the backend's get_current_user() already falls back to reading,
 *   so this still authenticates as the participant.
 * - This module only collects and transmits events; nothing here decides
 *   whether the participant/study has consented to tracking -- that's a
 *   protocol-level (IRB/consent form) concern that has to be handled before
 *   an admin ever flips these toggles on.
 */

import { WEBUI_API_BASE_URL } from '$lib/constants';
import { getModelTrackingConfig, sendResearchEmbedEvents } from '$lib/apis/researchEmbed';

type TrackedEventType = 'keystroke' | 'temporal_delay' | 'visibility' | 'clipboard';

type TrackingConfig = {
	track_keystrokes: boolean;
	track_temporal_delays: boolean;
	track_visibility: boolean;
	track_clipboard: boolean;
};

const FLUSH_INTERVAL_MS = 15000;
const MAX_BUFFERED_EVENTS = 50;
const MAX_CLIPBOARD_TEXT_LENGTH = 5000;

let initialized = false;
let buffer: Array<{
	event_type: TrackedEventType;
	chat_id: string | null;
	data: Record<string, unknown>;
	client_timestamp: number;
}> = [];

let flushTimer: ReturnType<typeof setInterval> | null = null;
let currentChatId: string | null = null;

// currentModelId is the model the chat page says it's using right now;
// trackingConfig is the last fetched tracking-config response for THAT
// model. They're tracked separately (rather than one "loading" flag) so a
// slow-to-resolve fetch for a model the user has since navigated away from
// can be safely ignored -- see setResearchEmbedTrackingModel.
let currentModelId: string | null = null;
let trackingConfig: TrackingConfig | null = null;

// Treated as "streaming" from the moment a message is sent until the
// response finishes -- an approximation (the true streaming window starts a
// little later, once the first token arrives) but close enough for
// "did the participant tab away while waiting on/reading a response".
let isStreamingActive = false;
let lastStreamFinishedAt: number | null = null;

let hiddenSince: number | null = null;
let hiddenStartedDuringStreaming = false;

function isTrackingEnabled(eventType: TrackedEventType): boolean {
	if (!trackingConfig) return false;

	switch (eventType) {
		case 'keystroke':
			return !!trackingConfig.track_keystrokes;
		case 'temporal_delay':
			return !!trackingConfig.track_temporal_delays;
		case 'visibility':
			return !!trackingConfig.track_visibility;
		case 'clipboard':
			return !!trackingConfig.track_clipboard;
		default:
			return false;
	}
}

function pushEvent(eventType: TrackedEventType, data: Record<string, unknown>) {
	if (!isTrackingEnabled(eventType)) return;

	buffer.push({
		event_type: eventType,
		chat_id: currentChatId,
		data,
		client_timestamp: Date.now()
	});

	if (buffer.length >= MAX_BUFFERED_EVENTS) {
		flush();
	}
}

function flush(useBeacon = false) {
	if (buffer.length === 0) return;

	const events = buffer;
	buffer = [];

	if (useBeacon && typeof navigator !== 'undefined' && navigator.sendBeacon) {
		try {
			const blob = new Blob([JSON.stringify({ model_id: currentModelId, events })], {
				type: 'application/json'
			});
			const delivered = navigator.sendBeacon(`${WEBUI_API_BASE_URL}/research-embed/events`, blob);
			if (delivered) return;
		} catch (e) {
			console.log('research embed tracking: sendBeacon failed, falling back to fetch', e);
		}
	}

	const token = localStorage.token;
	if (!token) return;

	sendResearchEmbedEvents(token, currentModelId, events);
}

/**
 * Sets up the periodic flush and the global visibility listener. Safe to
 * call more than once (e.g. Chat.svelte mounting again after navigation) --
 * only the first call does anything. Should be called once per page load,
 * not gated on chatOnly: the ingest endpoint accepts events from any
 * signed-in user, and whether tracking actually happens is entirely
 * controlled by the admin toggles checked above.
 */
export function initResearchEmbedTracking() {
	if (initialized || typeof window === 'undefined') return;
	initialized = true;

	flushTimer = setInterval(() => flush(false), FLUSH_INTERVAL_MS);

	window.addEventListener('pagehide', () => flush(true));
	window.addEventListener('beforeunload', () => flush(true));

	document.addEventListener('visibilitychange', () => {
		if (document.hidden) {
			hiddenSince = Date.now();
			hiddenStartedDuringStreaming = isStreamingActive;
		} else if (hiddenSince !== null) {
			if (hiddenStartedDuringStreaming) {
				pushEvent('visibility', { hidden_ms: Date.now() - hiddenSince });
			}
			hiddenSince = null;
			hiddenStartedDuringStreaming = false;
			// A visibility change is also a natural, low-overhead moment to
			// flush whatever's buffered instead of waiting for the next timer
			// tick -- the participant coming back is a reasonable point to
			// make sure recent data isn't sitting only in memory.
			flush(false);
		}
	});
}

export function setResearchEmbedTrackingChatId(chatId: string | null) {
	currentChatId = chatId ?? null;
}

/**
 * Fetches and caches the tracking config (the four track_* booleans) for
 * whichever model the chat page is currently using. Call this whenever the
 * selected model changes (Chat.svelte does this reactively off
 * selectedModels[0]) -- a no-op if the model id hasn't actually changed, so
 * it's safe to call on every reactive update rather than only on real
 * transitions. Fails closed: until the fetch resolves (or if it fails),
 * isTrackingEnabled() returns false for every event type, same as "no
 * tracking configured at all."
 */
export function setResearchEmbedTrackingModel(modelId: string | null) {
	const normalized = modelId ?? null;
	if (normalized === currentModelId) return;

	currentModelId = normalized;
	trackingConfig = null;

	if (!normalized || typeof localStorage === 'undefined' || !localStorage.token) return;

	const requestedModelId = normalized;
	getModelTrackingConfig(localStorage.token, requestedModelId).then((cfg) => {
		// The user may have switched models again before this resolved --
		// don't let a stale response overwrite a newer model's config.
		if (currentModelId === requestedModelId && cfg) {
			trackingConfig = cfg;
		}
	});
}

// Coarse key category only -- deliberately never the actual key/character,
// so keystroke *timing* can be analyzed without capturing message content
// (that's what the chat transcript itself already covers).
function categorizeKey(event: KeyboardEvent): string {
	if (event.key === 'Backspace' || event.key === 'Delete') return 'delete';
	if (event.key === 'Enter') return 'enter';
	if (event.key === ' ') return 'space';
	if (event.key.startsWith('Arrow')) return 'arrow';
	if (event.key.length === 1) return 'character';
	return 'other';
}

export function recordKeystrokeEvent(phase: 'keydown' | 'keyup', event: KeyboardEvent) {
	if (!isTrackingEnabled('keystroke')) return;
	pushEvent('keystroke', {
		phase,
		key_category: categorizeKey(event),
		repeat: !!event.repeat
	});
}

export function recordClipboardEvent(action: 'copy' | 'paste', event: ClipboardEvent) {
	if (!isTrackingEnabled('clipboard')) return;

	const clipboardData = event.clipboardData || (window as any).clipboardData;
	let text = clipboardData?.getData ? clipboardData.getData('text/plain') || clipboardData.getData('text') : '';
	if (typeof text !== 'string') text = '';
	if (text.length > MAX_CLIPBOARD_TEXT_LENGTH) {
		text = text.slice(0, MAX_CLIPBOARD_TEXT_LENGTH);
	}

	pushEvent('clipboard', { action, text });
}

/** Call when the participant submits a message (before/at the request that
 * kicks off a new response). Records the delay since the previous response
 * finished streaming, then marks a new streaming window as active. */
export function onResearchEmbedMessageSent() {
	if (isTrackingEnabled('temporal_delay') && lastStreamFinishedAt !== null) {
		pushEvent('temporal_delay', { delay_ms: Date.now() - lastStreamFinishedAt });
	}
	lastStreamFinishedAt = null;
	isStreamingActive = true;
}

/** Call when a response finishes streaming (the `done` branch of Chat.svelte's
 * chat:completion handler). */
export function onResearchEmbedStreamingDone() {
	isStreamingActive = false;
	lastStreamFinishedAt = Date.now();
}
