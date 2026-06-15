window.ODYSSEUS_OBSIDIAN_STANDALONE = true;

await import('/api/plugins/obsidian/web/main.js');

const openObsidian = () => {
  window.OdysseusObsidian?.openPanel?.();
};

if (document.readyState === 'loading') {
  window.addEventListener('DOMContentLoaded', openObsidian, { once: true });
} else {
  openObsidian();
}
