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
// Deliberately swallows errors and returns null instead of throwing --
// tracking is best-effort telemetry, a dropped batch (offline participant,
// server hiccup) should never surface an error to the participant or block
// the chat UI.
export const sendResearchEmbedEvents = async (token: string, events: object[]) => {
	const res = await fetch(`${WEBUI_API_BASE_URL}/research-embed/events`, {
		method: 'POST',
		headers: {
			'Content-Type': 'application/json',
			Authorization: `Bearer ${token}`
		},
		body: JSON.stringify({ events })
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
