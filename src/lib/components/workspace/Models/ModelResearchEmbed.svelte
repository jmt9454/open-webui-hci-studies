<script lang="ts">
	import { getContext, onMount } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { getModelEmbedCode } from '$lib/apis/researchEmbed';
	import { copyToClipboard } from '$lib/utils';

	import Switch from '$lib/components/common/Switch.svelte';
	import Textarea from '$lib/components/common/Textarea.svelte';
	import Spinner from '$lib/components/common/Spinner.svelte';

	const i18n = getContext('i18n');

	// Bound to info.meta.research_embed in ModelEditor.svelte, the same way
	// Capabilities.svelte binds to info.meta.capabilities. Admin-only: this
	// component should only ever be rendered for an admin (enforced in
	// ModelEditor.svelte) -- enabling research embed opens an unauthenticated
	// public entry point that creates real accounts and spends this model's
	// API budget, and the server independently refuses to let a non-admin
	// change this via a direct API call either (see
	// backend/open_webui/routers/models.py's _enforce_research_embed_admin_only).
	export let research_embed: { enabled?: boolean; seed_message?: string } = {};

	// Only a saved, real model has an id the embed URL can point at -- while
	// creating a brand-new model, this section can't generate a link yet
	// (same UX constraint as "share"/"clone" on unsaved entities elsewhere in
	// this app). Passed down from ModelEditor.svelte as `edit && model`.
	export let modelId: string = '';
	export let isPersisted: boolean = false;

	let embedCode = null;
	let loadingEmbedCode = false;

	const loadEmbedCode = async () => {
		if (!isPersisted || !modelId) return;

		loadingEmbedCode = true;
		embedCode = await getModelEmbedCode(localStorage.token, modelId).catch((error) => {
			toast.error(`${error}`);
			return null;
		});
		loadingEmbedCode = false;
	};

	onMount(() => {
		loadEmbedCode();
	});
</script>

<div>
	<div class="flex w-full justify-between mb-1">
		<div class=" self-center text-sm font-semibold">{$i18n.t('Research Embed')}</div>
	</div>

	<div class="text-xs text-gray-500 mb-2">
		{$i18n.t(
			'Runs this model as a chat-only research tool embedded in a survey platform (e.g. Qualtrics). Each participant gets their own account and single chat via the standalone entry service. Admin-only -- see Admin Panel > Settings > Research Embed for the participant-ID format and allowed embed origin, which apply to every study on this instance.'
		)}
	</div>

	<div class="flex w-full justify-between items-center pr-2 mb-2">
		<div class=" self-center text-xs font-medium">{$i18n.t('Enabled')}</div>
		<Switch bind:state={research_embed.enabled} />
	</div>

	{#if research_embed?.enabled}
		<div class="mb-2">
			<div class=" mb-1 text-xs font-medium">{$i18n.t('Seed Message')}</div>
			<Textarea
				className="w-full rounded-lg px-2.5 py-1.5 text-xs bg-gray-50 dark:bg-gray-900 outline-hidden resize-none"
				rows={2}
				placeholder={$i18n.t(
					'Leave empty to start participants on a blank chat instead of an automatic first message'
				)}
				bind:value={research_embed.seed_message}
			/>
		</div>

		{#if !isPersisted}
			<div class="text-xs text-gray-500 mb-2">
				{$i18n.t('Save this model first to generate its embed link.')}
			</div>
		{:else}
			<div class="mb-1">
				<div class="flex justify-between items-center mb-1">
					<div class=" text-xs font-medium">{$i18n.t('Embed Code')}</div>
					<button
						type="button"
						class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 underline flex items-center gap-1"
						on:click={loadEmbedCode}
						disabled={loadingEmbedCode}
					>
						{#if loadingEmbedCode}
							<Spinner className="size-3" />
						{/if}
						{$i18n.t('Refresh')}
					</button>
				</div>

				<div class="text-xs text-gray-500 mb-2">
					{$i18n.t(
						'Reflects the currently saved model -- save changes above, then refresh to update the link.'
					)}
				</div>

				{#if loadingEmbedCode && !embedCode}
					<div class="text-xs text-gray-500">{$i18n.t('Loading...')}</div>
				{:else if embedCode}
					{#each embedCode.warnings as warning}
						<div
							class="mb-2 text-xs text-yellow-600 dark:text-yellow-500 bg-yellow-50 dark:bg-yellow-900/20 rounded-lg px-2.5 py-1.5"
						>
							{warning}
						</div>
					{/each}

					<div class="mb-2">
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

					<div class="mb-2">
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
		{/if}
	{/if}
</div>
