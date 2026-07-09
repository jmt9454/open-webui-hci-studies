import { WEBUI_API_BASE_URL } from '$lib/constants';

export const getResearchEmbedConfig = async (token: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/research-embed/config`, {
		method: 'GET',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		}
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const updateResearchEmbedConfig = async (token: string, config: object) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/research-embed/config`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			...config
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

export const syncEntryService = async (token: string, apiKey: string) => {
	let error = null;

	const res = await fetch(`${WEBUI_API_BASE_URL}/research-embed/sync-entry-service`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({
			api_key: apiKey
		})
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

// Sends a batch of behavioral-tracking events (keystroke/temporal_delay/
// visibility/clipboard) recorded by src/lib/utils/researchEmbedTracking.ts.
// modelId is required now that tracking toggles are per-model -- the server
// looks up THAT model's track_* settings (Model.meta.research_embed) to
// decide what in the batch actually gets accepted, see
// backend/open_webui/routers/research_embed.py's POST /events. Deliberately
// swallows errors and returns null instead of throwing -- tracking is
// best-effort telemetry, a dropped batch (offline participant, server
// hiccup) should never surface an error to the participant or block the
// chat UI.
export const sendResearchEmbedEvents = async (
	token: string,
	modelId: string | null,
	events: object[]
) => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/research-embed/events`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({ model_id: modelId, events })
	})
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			return null;
		});

	return res;
};

// Per-model equivalents of the old global "Embed Code" section -- see
// futurefeature.md's "Per-Model Research Embed" design. Each model has its
// own independent enabled/seed_message/embed-code, edited from
// Workspace > Models > (edit a model) rather than the admin settings page.

export const getModelEmbedCode = async (token: string, modelId: string) => {
	let error = null;

	const res = await fetch(
		`${WEBUI_API_BASE_URL}/research-embed/models/${encodeURIComponent(modelId)}/embed-code`,
		{
			method: 'GET',
			headers: {
				'Content-Type': 'application/json',
				Authorization: `Bearer ${token}`
			}
		}
	)
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

// Called by ANY signed-in user's own browser (see
// src/lib/utils/researchEmbedTracking.ts), not just admins -- a participant's
// chat page needs to know whether tracking is on for the model it's using.
// Swallows errors and returns null instead of throwing, same reasoning as
// sendResearchEmbedEvents above: a failed lookup should just mean "don't
// track this session," not break the chat UI.
export const getModelTrackingConfig = async (token: string, modelId: string) => {
	const res = await fetch(
		`${WEBUI_API_BASE_URL}/research-embed/models/${encodeURIComponent(modelId)}/tracking-config`,
		{
			method: 'GET',
			headers: {
				'Content-Type': 'application/json',
				Authorization: `Bearer ${token}`
			}
		}
	)
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			return null;
		});

	return res;
};

// Admin-only. Backs the in-app data viewer (paginated JSON) and the
// per-model CSV download in ModelResearchEmbed.svelte -- see
// GET /models/{model_id}/events in research_embed.py for the two response
// shapes depending on `format`.
export const getModelEvents = async (
	token: string,
	modelId: string,
	{
		limit = 50,
		offset = 0,
		eventType = ''
	}: { limit?: number; offset?: number; eventType?: string } = {}
) => {
	let error = null;

	const params = new URLSearchParams({
		format: 'json',
		limit: String(limit),
		offset: String(offset)
	});
	if (eventType) params.set('event_type', eventType);

	const res = await fetch(
		`${WEBUI_API_BASE_URL}/research-embed/models/${encodeURIComponent(modelId)}/events?${params.toString()}`,
		{
			method: 'GET',
			headers: {
				'Content-Type': 'application/json',
				Authorization: `Bearer ${token}`
			}
		}
	)
		.then(async (res) => {
			if (!res.ok) throw await res.json();
			return res.json();
		})
		.catch((err) => {
			console.log(err);
			error = err.detail;
			return null;
		});

	if (error) {
		throw error;
	}

	return res;
};

// Builds the direct download URL for a model's full CSV export. Not a
// fetch-and-blob helper on purpose -- ModelResearchEmbed.svelte just points
// an <a href> at this and lets the browser handle the download, same as
// admins already do to hit this API's other GET endpoints directly (the
// "token" cookie set on login authenticates the navigation, no need to
// juggle an Authorization header for a plain link).
export const getModelEventsExportUrl = (modelId: string) =>
	`${WEBUI_API_BASE_URL}/research-embed/models/${encodeURIComponent(modelId)}/events?format=csv`;
