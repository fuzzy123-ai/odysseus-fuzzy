window.ODYSSEUS_OBSIDIAN_STANDALONE = true;

const pluginApiPrefix = window.location.pathname.startsWith('/api/plugins/orca/')
  ? '/api/plugins/orca'
  : '/api/plugins/obsidian';

await import(`${pluginApiPrefix}/web/main.js`);

const openObsidian = () => {
  window.OdysseusObsidian?.openPanel?.();
};

if (document.readyState === 'loading') {
  window.addEventListener('DOMContentLoaded', openObsidian, { once: true });
} else {
  openObsidian();
}
