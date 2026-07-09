<script lang="ts">
	import { getContext } from 'svelte';
	import { toast } from 'svelte-sonner';

	import { getModelEvents, getModelEventsExportUrl } from '$lib/apis/researchEmbed';
	import Spinner from '$lib/components/common/Spinner.svelte';
	import ChevronLeft from '$lib/components/icons/ChevronLeft.svelte';
	import ChevronRight from '$lib/components/icons/ChevronRight.svelte';

	const i18n = getContext('i18n');

	// Admin-only in-app viewer for this model's behavioral-tracking events
	// (see GET /models/{model_id}/events in research_embed.py) plus a link to
	// the full CSV download. Rendered by ModelResearchEmbed.svelte only once
	// a model is persisted (there's no data to view before then). Data is
	// fetched lazily -- opening the model editor shouldn't always hit the
	// events table, only when someone actually wants to look.
	export let modelId: string;

	const EVENT_TYPES = ['keystroke', 'temporal_delay', 'visibility', 'clipboard'];
	const PAGE_SIZE = 25;

	let expanded = false;
	let loading = false;
	let loaded = false;
	let events: Array<{
		id: string;
		user_id: string;
		chat_id: string | null;
		event_type: string;
		data: Record<string, unknown>;
		client_timestamp: number | null;
		created_at: number;
	}> = [];
	let total = 0;
	let offset = 0;
	let eventTypeFilter = '';

	const loadEvents = async () => {
		loading = true;
		const res = await getModelEvents(localStorage.token, modelId, {
			limit: PAGE_SIZE,
			offset,
			eventType: eventTypeFilter
		}).catch((error) => {
			toast.error(`${error}`);
			return null;
		});

		if (res) {
			events = res.events ?? [];
			total = res.total ?? 0;
			loaded = true;
		}
		loading = false;
	};

	const toggleExpanded = () => {
		expanded = !expanded;
		if (expanded && !loaded) {
			loadEvents();
		}
	};

	const onFilterChange = () => {
		offset = 0;
		loadEvents();
	};

	const nextPage = () => {
		offset += PAGE_SIZE;
		loadEvents();
	};

	const prevPage = () => {
		offset = Math.max(0, offset - PAGE_SIZE);
		loadEvents();
	};

	const formatTimestamp = (ms: number | null) => {
		if (!ms) return '';
		return new Date(ms).toLocaleString();
	};
</script>

<div class="mb-1">
	<div class="flex justify-between items-center mb-1">
		<div class=" text-xs font-medium">{$i18n.t('Tracking Data')}</div>
		<div class="flex items-center gap-3">
			<a
				href={getModelEventsExportUrl(modelId)}
				target="_blank"
				rel="noopener noreferrer"
				class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 underline"
			>
				{$i18n.t('Download CSV')}
			</a>
			<button
				type="button"
				class="text-xs text-gray-500 hover:text-gray-700 dark:hover:text-gray-300 underline"
				on:click={toggleExpanded}
			>
				{expanded ? $i18n.t('Hide') : $i18n.t('View Data')}
			</button>
		</div>
	</div>

	{#if expanded}
		<div class="text-xs text-gray-500 mb-2">
			{$i18n.t(
				'Every behavioral-tracking event recorded for THIS model, oldest first. The CSV download always has everything -- this view is just for a quick look.'
			)}
		</div>

		<div class="flex justify-between items-center mb-2">
			<select
				class="text-xs bg-gray-50 dark:bg-gray-900 rounded-lg px-2 py-1 outline-hidden"
				bind:value={eventTypeFilter}
				on:change={onFilterChange}
			>
				<option value="">{$i18n.t('All event types')}</option>
				{#each EVENT_TYPES as type}
					<option value={type}>{type}</option>
				{/each}
			</select>

			{#if loaded}
				<div class="text-xs text-gray-500 flex items-center gap-1">
					{#if total === 0}
						{$i18n.t('No events yet')}
					{:else}
						{offset + 1}&ndash;{Math.min(offset + PAGE_SIZE, total)} {$i18n.t('of')} {total}
					{/if}

					<button
						type="button"
						class="p-1 disabled:opacity-30"
						on:click={prevPage}
						disabled={offset === 0 || loading}
					>
						<ChevronLeft className="size-3" />
					</button>
					<button
						type="button"
						class="p-1 disabled:opacity-30"
						on:click={nextPage}
						disabled={offset + PAGE_SIZE >= total || loading}
					>
						<ChevronRight className="size-3" />
					</button>
				</div>
			{/if}
		</div>

		{#if loading}
			<div class="flex justify-center py-4">
				<Spinner className="size-4" />
			</div>
		{:else if events.length === 0}
			<div class="text-xs text-gray-500 py-2">
				{$i18n.t(
					'No tracking events recorded for this model yet -- make sure at least one Behavioral Tracking toggle above is on, then check back after a participant session.'
				)}
			</div>
		{:else}
			<div class="overflow-x-auto border border-gray-100 dark:border-gray-850 rounded-lg">
				<table class="w-full text-xs">
					<thead class="bg-gray-50 dark:bg-gray-900 text-gray-500">
						<tr>
							<th class="text-left px-2 py-1 font-medium">{$i18n.t('Time')}</th>
							<th class="text-left px-2 py-1 font-medium">{$i18n.t('Type')}</th>
							<th class="text-left px-2 py-1 font-medium">{$i18n.t('Chat')}</th>
							<th class="text-left px-2 py-1 font-medium">{$i18n.t('Data')}</th>
						</tr>
					</thead>
					<tbody>
						{#each events as event (event.id)}
							<tr class="border-t border-gray-100 dark:border-gray-850">
								<td class="px-2 py-1 whitespace-nowrap align-top"
									>{formatTimestamp(event.client_timestamp)}</td
								>
								<td class="px-2 py-1 whitespace-nowrap align-top">{event.event_type}</td>
								<td class="px-2 py-1 whitespace-nowrap align-top font-mono"
									>{(event.chat_id ?? '').slice(0, 8)}</td
								>
								<td class="px-2 py-1 align-top font-mono break-all" title={JSON.stringify(event.data)}
									>{JSON.stringify(event.data)}</td
								>
							</tr>
						{/each}
					</tbody>
				</table>
			</div>
		{/if}
	{/if}
</div>
