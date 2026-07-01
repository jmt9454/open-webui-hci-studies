<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { onMount, getContext } from 'svelte';

	import { models } from '$lib/stores';
	import {
		getResearchEmbedConfig,
		updateResearchEmbedConfig,
		getResearchEmbedCode
	} from '$lib/apis/researchEmbed';
	import { copyToClipboard } from '$lib/utils';

	import Textarea from '$lib/components/common/Textarea.svelte';

	const i18n = getContext('i18n');

	export let saveHandler: Function;

	let config = null;

	let embedCode = null;
	let loadingEmbedCode = false;

	const submitHandler = async () => {
		const res = await updateResearchEmbedConfig(localStorage.token, config).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			config = res;
			// The embed code depends on the saved config (model, param name,
			// allowed origin), so refresh it after every successful save.
			await loadEmbedCode();
		}
	};

	const loadEmbedCode = async () => {
		loadingEmbedCode = true;
		embedCode = await getResearchEmbedCode(localStorage.token).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		loadingEmbedCode = false;
	};

	onMount(async () => {
		const res = await getResearchEmbedConfig(localStorage.token).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			config = res;
		}

		await loadEmbedCode();
	});
</script>

<form
	class="flex flex-col h-full justify-between space-y-3 text-sm"
	on:submit|preventDefault={async () => {
		await submitHandler();
		saveHandler();
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
							'Lets this instance run as a chat-only research tool embedded in a survey platform (e.g. Qualtrics). Each participant gets their own account and single chat via a standalone entry service, without a sidebar or settings.'
						)}
					</div>

					<hr class=" border-gray-100 dark:border-gray-850 my-2" />

					<div class="mb-2.5">
						<div class=" mb-1 text-xs font-medium">{$i18n.t('Model')}</div>
						<div class="flex items-center relative">
							<select
								class="dark:bg-gray-900 w-full rounded-lg px-2.5 py-1.5 text-xs bg-gray-50 outline-hidden"
								bind:value={config.RESEARCH_EMBED_MODEL_ID}
							>
								<option value="">{$i18n.t('Select a model')}</option>
								{#each $models.filter((m) => !(m?.info?.meta?.hidden ?? false)) as model}
									<option value={model.id}>{model.name}</option>
								{/each}
							</select>
						</div>
						<div class="text-xs text-gray-500 mt-1">
							{$i18n.t(
								"Remember to also grant this model's access control to non-admin users (Admin Panel > Settings > Models), otherwise participant accounts won't be able to use it."
							)}
						</div>
					</div>

					<div class="mb-2.5">
						<div class=" mb-1 text-xs font-medium">{$i18n.t('Seed Message')}</div>
						<Textarea
							className="w-full rounded-lg px-2.5 py-1.5 text-xs bg-gray-50 dark:bg-gray-900 outline-hidden resize-none"
							rows={2}
							placeholder={$i18n.t(
								"Leave empty to start participants on a blank chat instead of an automatic first message"
							)}
							bind:value={config.RESEARCH_EMBED_SEED_MESSAGE}
						/>
					</div>

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
								'The URL query parameter your survey platform uses for the participant/response ID, e.g. "pid" for a generic link or "PROLIFIC_PID" for Prolific.'
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
								'The exact origin your survey platform serves the embedding page from (scheme + host, no path). Sets Content-Security-Policy: frame-ancestors so browsers allow the iframe -- check your survey\'s share link, some institutions are on a subdomain like yourschool.co1.qualtrics.com. Leave empty and most browsers will refuse to render the embed at all.'
							)}
						</div>
					</div>
				</div>

				<div class="mb-3.5">
					<div class=" mb-2.5 text-base font-medium">
						{$i18n.t('Embed Code')}
					</div>

					<hr class=" border-gray-100 dark:border-gray-850 my-2" />

					{#if loadingEmbedCode}
						<div class="text-xs text-gray-500">{$i18n.t('Loading...')}</div>
					{:else if embedCode}
						{#each embedCode.warnings as warning}
							<div
								class="mb-2 text-xs text-yellow-600 dark:text-yellow-500 bg-yellow-50 dark:bg-yellow-900/20 rounded-lg px-2.5 py-1.5"
							>
								{warning}
							</div>
						{/each}

						<div class="mb-2.5">
							<div class="flex justify-between items-center mb-1">
								<div class=" text-xs font-medium">{$i18n.t('Qualtrics Entry URL')}</div>
								<button
									type="button"
									class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 underline"
									on:click={() => {
										copyToClipboard(embedCode.entry_url);
										toast.success($i18n.t('Copied to clipboard'));
									}}
								>
									{$i18n.t('Copy')}
								</button>
							</div>
							<input
								class="w-full rounded-lg px-2.5 py-1.5 text-xs bg-gray-50 dark:bg-gray-900 outline-hidden font-mono"
								type="text"
								readonly
								value={embedCode.entry_url}
								on:click={(e) => e.currentTarget.select()}
							/>
							<div class="text-xs text-gray-500 mt-1">
								{$i18n.t(
									'Paste this directly as a Qualtrics "Rich Content Editor > Source Code" link, or use the iframe snippet below to embed it inline in a question.'
								)}
							</div>
						</div>

						<div class="mb-2.5">
							<div class="flex justify-between items-center mb-1">
								<div class=" text-xs font-medium">{$i18n.t('Iframe Snippet')}</div>
								<button
									type="button"
									class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 underline"
									on:click={() => {
										copyToClipboard(embedCode.iframe_snippet);
										toast.success($i18n.t('Copied to clipboard'));
									}}
								>
									{$i18n.t('Copy')}
								</button>
							</div>
							<textarea
								class="w-full rounded-lg px-2.5 py-1.5 text-xs bg-gray-50 dark:bg-gray-900 outline-hidden resize-none font-mono"
								rows="3"
								readonly
								value={embedCode.iframe_snippet}
								on:click={(e) => e.currentTarget.select()}
							/>
						</div>
					{/if}
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
