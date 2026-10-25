(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.HarborForgeApi = api;
})(typeof window !== 'undefined' ? window : globalThis, function () {
  'use strict';

  const MAX_RESPONSE_BYTES = 256 * 1024;
  const MAX_REPOS = 64;
  const MAX_VERSIONS = 256;
  const MAX_TEXT = 512;
  const REQUEST_TIMEOUT_MS = 8000;

  class ForgeApiError extends Error {
    constructor(code, message, status) {
      super(message);
      this.name = 'ForgeApiError';
      this.code = code;
      this.status = Number(status) || 0;
    }
  }

  function text(value) {
    return typeof value === 'string' && value.length <= MAX_TEXT ? value.trim() : '';
  }

  function requiredText(value, label) {
    const result = text(value);
    if (!result) throw new ForgeApiError('invalid_payload', `${label} is invalid`);
    return result;
  }

  function identifier(value) {
    const result = text(value);
    if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/.test(result)) {
      throw new ForgeApiError('invalid_repo_id', 'Repository identifier is invalid');
    }
    return encodeURIComponent(result);
  }

  function array(value) { return Array.isArray(value) ? value : []; }

  function assertReposPayload(payload) {
    if (!payload || typeof payload !== 'object' || !Array.isArray(payload.repos) || payload.repos.length > MAX_REPOS) {
      throw new ForgeApiError('invalid_repos_payload', 'Repository list was not understood');
    }
    const repos = payload.repos.map((item, index) => {
      if (!item || typeof item !== 'object') throw new ForgeApiError('invalid_repos_payload', `Repository ${index + 1} is invalid`);
      requiredText(item.repo_id, `Repository ${index + 1} id`);
      requiredText(item.title, `Repository ${index + 1} title`);
      return item;
    });
    if (new Set(repos.map(item => item.repo_id)).size !== repos.length) {
      throw new ForgeApiError('invalid_repos_payload', 'Repository list contains duplicate identifiers');
    }
    return { ...payload, repos };
  }

  function assertStatusPayload(payload, expectedRepoId) {
    if (!payload || typeof payload !== 'object' || !payload.repo || !payload.git) {
      throw new ForgeApiError('invalid_status_payload', 'Repository status was not understood');
    }
    if (requiredText(payload.repo.repo_id, 'Repository status id') !== expectedRepoId || requiredText(payload.git.repo_id, 'Git status id') !== expectedRepoId) {
      throw new ForgeApiError('repo_mismatch', 'Repository status did not match the selected repository');
    }
    requiredText(payload.git.current_branch || payload.repo.current_branch || payload.repo.default_branch || payload.git.status?.branch_line, 'Repository branch');
    if (!payload.git.status || typeof payload.git.status !== 'object' || typeof payload.git.status.dirty !== 'boolean' || !Array.isArray(payload.git.status.entries) || payload.git.status.entries.length > MAX_VERSIONS) {
      throw new ForgeApiError('invalid_status_payload', 'Git status was not understood');
    }
    return payload;
  }

  function assertPolicyPayload(payload, expectedRepoId) {
    if (!payload || typeof payload !== 'object' || !payload.policy || typeof payload.policy !== 'object') {
      throw new ForgeApiError('invalid_policy_payload', 'Forge policy was not understood');
    }
    if (requiredText(payload.repo_id, 'Forge policy repository id') !== expectedRepoId || !['local', 'github', 'nextcloud'].includes(payload.policy.forge_mode)) {
      throw new ForgeApiError('repo_mismatch', 'Forge policy did not match the selected repository');
    }
    if (typeof payload.policy.sync_on_commit !== 'boolean' || !Array.isArray(payload.policy.backup_providers) || payload.policy.backup_providers.length > MAX_REPOS) {
      throw new ForgeApiError('invalid_policy_payload', 'Forge policy was not understood');
    }
    return payload;
  }

  function assertVersionsPayload(payload, expectedRepoId) {
    if (!payload || typeof payload !== 'object' || requiredText(payload.repo_id, 'Project versions repository id') !== expectedRepoId || !Array.isArray(payload.versions) || payload.versions.length > MAX_VERSIONS) {
      throw new ForgeApiError('invalid_versions_payload', 'Project versions were not understood');
    }
    payload.versions.forEach((version, index) => {
      if (!version || typeof version !== 'object' || !requiredText(version.version_id, `Version ${index + 1} id`) || !requiredText(version.created_at, `Version ${index + 1} timestamp`) || Number.isNaN(Date.parse(version.created_at)) || version.verified !== true) {
        throw new ForgeApiError('invalid_versions_payload', `Version ${index + 1} is invalid`);
      }
    });
    return payload;
  }

  class ForgeApi {
    constructor(options) {
      const settings = options || {};
      this.fetchImpl = settings.fetchImpl || (typeof fetch === 'function' ? fetch.bind(globalThis) : null);
      this.timeoutMs = Math.max(100, Math.min(Number(settings.timeoutMs) || REQUEST_TIMEOUT_MS, REQUEST_TIMEOUT_MS));
      this.reposBase = String(settings.reposBase || '/api/repos').replace(/\/$/, '');
      this.versioningBase = String(settings.versioningBase || '/api/project-versioning').replace(/\/$/, '');
      if (!this.fetchImpl) throw new ForgeApiError('fetch_unavailable', 'Repository status is unavailable');
    }

    async request(path) {
      let response;
      const controller = typeof AbortController === 'function' ? new AbortController() : null;
      const timeout = controller ? setTimeout(() => controller.abort(), this.timeoutMs) : null;
      try {
        response = await this.fetchImpl(path, { credentials: 'same-origin', headers: { Accept: 'application/json' }, signal: controller?.signal });
        const contentLength = Number(response.headers?.get?.('content-length'));
        if (Number.isFinite(contentLength) && contentLength > MAX_RESPONSE_BYTES) {
          controller?.abort();
          throw new ForgeApiError('response_too_large', 'Repository status response was too large');
        }
        if (!response.ok) {
          const code = response.status === 401 || response.status === 403 ? 'unauthorized' : 'request_failed';
          throw new ForgeApiError(code, code === 'unauthorized' ? 'Repository access is unavailable' : 'Repository status is unavailable', response.status);
        }
        let body = '';
        if (response.body?.getReader) {
          const reader = response.body.getReader();
          const decoder = new TextDecoder();
          let bytes = 0;
          const chunks = [];
          try {
            while (true) {
              const part = await reader.read();
              if (part.done) break;
              bytes += part.value.byteLength;
              if (bytes > MAX_RESPONSE_BYTES) {
                await reader.cancel();
                controller?.abort();
                throw new ForgeApiError('response_too_large', 'Repository status response was too large');
              }
              chunks.push(decoder.decode(part.value, { stream: true }));
            }
            body = chunks.join('') + decoder.decode();
          } finally {
            reader.releaseLock?.();
          }
        } else {
          body = await response.text();
          if (new TextEncoder().encode(body).byteLength > MAX_RESPONSE_BYTES) throw new ForgeApiError('response_too_large', 'Repository status response was too large');
        }
        let payload = null;
        try { payload = JSON.parse(body); } catch { /* response body is intentionally not surfaced */ }
        return payload;
      } catch (error) {
        if (error instanceof ForgeApiError) throw error;
        if (error?.name === 'AbortError') throw new ForgeApiError('request_timeout', 'Repository status timed out');
        throw new ForgeApiError('network_error', 'Repository status could not be reached');
      } finally {
        if (timeout) clearTimeout(timeout);
      }
    }

    async listRepos() { return assertReposPayload(await this.request(this.reposBase)); }

    async readRepo(repoId) {
      const encoded = identifier(repoId);
      const [status, policy, versions] = await Promise.all([
        this.request(`${this.reposBase}/${encoded}/status`).then(value => assertStatusPayload(value, repoId)),
        this.request(`${this.versioningBase}/${encoded}/policy`).then(value => assertPolicyPayload(value, repoId)),
        this.request(`${this.versioningBase}/${encoded}/versions`).then(value => assertVersionsPayload(value, repoId))
      ]);
      const orderedVersions = versions.versions.slice().sort((left, right) => {
        const difference = Date.parse(right.created_at) - Date.parse(left.created_at);
        return Number.isNaN(difference) || difference === 0 ? String(right.version_id).localeCompare(String(left.version_id)) : difference;
      });
      return { ...status, policy: policy.policy, versions: orderedVersions };
    }

    async load(selectedRepoId) {
      const summary = await this.listRepos();
      const selected = summary.repos.find(repo => repo.repo_id === selectedRepoId) || (summary.repos.length === 1 ? summary.repos[0] : null);
      if (summary.repos.length > 1 && !selected) return { summary, selected: null, detail: null, requiresSelection: true };
      if (!selected) return { summary, selected: null, detail: null };
      return { summary, selected, detail: await this.readRepo(selected.repo_id) };
    }
  }

  return { ForgeApi, ForgeApiError, assertReposPayload, assertStatusPayload, assertPolicyPayload, assertVersionsPayload };
});
