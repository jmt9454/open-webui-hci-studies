<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { onMount, getContext } from 'svelte';

	import { getResearchEmbedConfig, updateResearchEmbedConfig, syncEntryService } from '$lib/apis/researchEmbed';
	import { createAPIKey } from '$lib/apis/auths';

	import Spinner from '$lib/components/common/Spinner.svelte';

	const i18n = getContext('i18n');

	export let saveHandler: Function;

	let config = null;

	let connectingEntryService = false;

	// Returns true/false so the caller (the form's submit handler below) only
	// fires the generic "Settings saved successfully!" toast (saveHandler(),
	// passed in from the parent Settings page) when the save actually
	// worked -- previously it ran unconditionally, so a validation error on
	// this form still showed a misleading success toast alongside the real
	// error.
	const submitHandler = async () => {
		const res = await updateResearchEmbedConfig(localStorage.token, config).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			config = res;
			return true;
		}

		return false;
	};

	const connectEntryServiceHandler = async () => {
		connectingEntryService = true;

		// Generates a fresh API key for *this* admin account (already-existing
		// endpoint, POST /api/v1/auths/api_key) and hands it to the entry
		// service over the internal Docker network -- no .env editing, no
		// container restart. See backend/open_webui/routers/research_embed.py
		// and entry-service/entry_service.py's POST /internal/admin-key.
		// createAPIKey() returns the key as a bare string (see
		// src/lib/apis/auths/index.ts), not an object -- don't check
		// `keyRes?.api_key` here, keyRes IS the api_key.
		const keyRes = await createAPIKey(localStorage.token).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (keyRes) {
			const syncRes = await syncEntryService(localStorage.token, keyRes).catch((error) => {
				toast.error(`${error}`);
				return null;
			});

			if (syncRes) {
				toast.success($i18n.t('Entry service connected.'));
			}
		} else {
			toast.error($i18n.t('Failed to generate an API key for this account.'));
		}

		connectingEntryService = false;
	};

	onMount(async () => {
		const res = await getResearchEmbedConfig(localStorage.token).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			config = res;
		}
	});
</script>

<form
	class="flex flex-col h-full justify-between space-y-3 text-sm"
	on:submit|preventDefault={async () => {
		const saved = await submitHandler();
		if (saved) {
			saveHandler();
		}
	}}
>
	<div class=" space-y-3 overflow-y-scroll scrollbar-hidden h-full">
		{#if config}
			<div>
				<div class="mb-3.5">
					<div class=" mb-2.5 text-base font-medium">
						{$i18n.t('Research Embed')}
					</div>

					<div class="mb-2.5 text-xs text-gray-500">
						{$i18n.t(
							'Lets one or more models on this instance run as a chat-only research tool embedded in a survey platform (e.g. Qualtrics). Each participant gets their own account and single chat via a standalone entry service, without a sidebar or settings.'
						)}
					</div>

					<div class="mb-2.5 text-xs text-gray-500 bg-gray-50 dark:bg-gray-850 rounded-lg px-2.5 py-2">
						{$i18n.t(
							'Which model to use, its seed message, and its generated embed link are no longer configured here -- each model has its own independent research embed settings, so more than one study or condition can run on this instance at once. Go to Workspace > Models, edit (or create) the model for your study, and look for the "Research Embed" section (admin-only).'
						)}
					</div>

					<hr class=" border-gray-100 dark:border-gray-850 my-2" />

					<div class="mb-2.5">
						<div class=" mb-1 text-xs font-medium">{$i18n.t('Participant ID Parameter Name')}</div>
						<input
							class="w-full rounded-lg px-2.5 py-1.5 text-xs bg-gray-50 dark:bg-gray-900 outline-hidden"
							type="text"
							bind:value={config.RESEARCH_EMBED_PARTICIPANT_ID_PARAM}
							placeholder="pid"
						/>
						<div class="text-xs text-gray-500 mt-1">
							{$i18n.t(
								'The URL query parameter your survey platform uses for the participant/response ID, e.g. "pid" for a generic link or "PROLIFIC_PID" for Prolific. Shared by every study on this instance.'
							)}
						</div>
					</div>

					<div class="mb-2.5">
						<div class=" mb-1 text-xs font-medium">{$i18n.t('Participant ID Format')}</div>
						<input
							class="w-full rounded-lg px-2.5 py-1.5 text-xs bg-gray-50 dark:bg-gray-900 outline-hidden font-mono"
							type="text"
							bind:value={config.RESEARCH_EMBED_PARTICIPANT_ID_REGEX}
							placeholder={'^R_[a-zA-Z0-9]{15,32}$'}
						/>
						<div class="text-xs text-gray-500 mt-1">
							{$i18n.t(
								'A regular expression the participant ID must match before an account gets created. The default matches Qualtrics Response IDs.'
							)}
						</div>
					</div>

					<div class="mb-2.5">
						<div class=" mb-1 text-xs font-medium">{$i18n.t('Participant Email Domain')}</div>
						<input
							class="w-full rounded-lg px-2.5 py-1.5 text-xs bg-gray-50 dark:bg-gray-900 outline-hidden"
							type="text"
							bind:value={config.RESEARCH_EMBED_PARTICIPANT_EMAIL_DOMAIN}
							placeholder="participants.local"
						/>
						<div class="text-xs text-gray-500 mt-1">
							{$i18n.t(
								'Participant accounts are created as {{PARAM}}@this-domain -- used only to tell participant accounts apart from staff accounts (chat-only mode is based on this, see the fork\'s +layout.svelte / Chat.svelte edits).',
								{ PARAM: config.RESEARCH_EMBED_PARTICIPANT_ID_PARAM || 'pid' }
							)}
						</div>
					</div>

					<div class="mb-2.5">
						<div class=" mb-1 text-xs font-medium">{$i18n.t('Allowed Embed Origin')}</div>
						<input
							class="w-full rounded-lg px-2.5 py-1.5 text-xs bg-gray-50 dark:bg-gray-900 outline-hidden"
							type="text"
							bind:value={config.RESEARCH_EMBED_ALLOWED_ORIGIN}
							placeholder="https://yourorg.qualtrics.com"
						/>
						<div class="text-xs text-gray-500 mt-1">
							{$i18n.t(
								'The exact origin your survey platform serves the embedding page from (scheme + host, no path). Sets Content-Security-Policy: frame-ancestors so browsers allow the iframe -- check your survey\'s share link, some institutions are on a subdomain like yourschool.co1.qualtrics.com. Leave empty and most browsers will refuse to render the embed at all. Shared by every study on this instance -- if multiple studies embed from different survey platforms, this needs to cover all of them.'
							)}
						</div>
					</div>
				</div>

				<div class="mb-2.5 text-xs text-gray-500 bg-gray-50 dark:bg-gray-850 rounded-lg px-2.5 py-2">
					{$i18n.t(
						'Behavioral tracking (keystroke dynamics, temporal delays, tab visibility, copy/paste) is also configured per model now, not here -- each model\'s Research Embed section (Workspace > Models) has its own four toggles, so different studies can enable different tracking independently.'
					)}
				</div>

				<div class="mb-3.5">
					<div class=" mb-2.5 text-base font-medium">
						{$i18n.t('Connect Entry Service')}
					</div>

					<hr class=" border-gray-100 dark:border-gray-850 my-2" />

					<div class="text-xs text-gray-500 mb-2.5">
						{$i18n.t(
							'The standalone entry service (Part 3) needs an admin API key to create participant accounts. Click below to generate one for your account and push it to the entry service automatically -- no .env editing or container restart needed. Safe to click again any time (e.g. after rotating keys). Shared by every study on this instance -- only needs doing once.'
						)}
					</div>

					<button
						type="button"
						class="px-3.5 py-1.5 text-xs font-medium bg-gray-50 hover:bg-gray-100 dark:bg-gray-850 dark:hover:bg-gray-800 dark:text-white transition rounded-full flex items-center gap-2"
						disabled={connectingEntryService}
						on:click={connectEntryServiceHandler}
					>
						{#if connectingEntryService}
							<Spinner className="size-3.5" />
						{/if}
						{$i18n.t('Connect Entry Service')}
					</button>
				</div>
			</div>
		{/if}
	</div>

	<div class="flex justify-end pt-3 text-sm font-medium">
		<button
			class="px-3.5 py-1.5 text-sm font-medium bg-black hover:bg-gray-900 text-white dark:bg-white dark:text-black dark:hover:bg-gray-100 transition rounded-full"
			type="submit"
		>
			{$i18n.t('Save')}
		</button>
	</div>
</form>
