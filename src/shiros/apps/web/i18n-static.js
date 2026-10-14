/* Runs before application modules, against the shipped template only. */
window.ShirosI18n.scanStatic(document.body);
window.ShirosI18n.text(document.querySelector('title'), window.ShirosI18n.message('ShirOS · 个人知识管理'));
