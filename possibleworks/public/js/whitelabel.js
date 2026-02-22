/**
 * Possibleworks White-Label v4.0.0
 * ─────────────────────────────────
 * Replaces Frappe branding with Possibleworks in the Desk UI.
 *
 * Strategy:
 *   1. One-time patches at DOMContentLoaded (prototype overrides, boot filtering)
 *   2. A single debounced MutationObserver for reactive DOM text replacement
 *
 * No setInterval, no triple-setTimeout. Clean and efficient.
 */
(function () {
	"use strict";

	const BRAND = "Possibleworks";

	// ── DOM Text Replacement ────────────────────────────────────────
	const TEXT_REPLACEMENTS = [
		["Frappe Frameworks", BRAND],
		["Frappe Framework", BRAND],
	];

	function replaceInVisibleText(node) {
		if (!node) return;
		if (node.nodeType === Node.TEXT_NODE) {
			const parent = node.parentNode;
			if (parent && parent.tagName !== "SCRIPT" && parent.tagName !== "STYLE") {
				let text = node.textContent;
				let changed = false;
				for (const [from, to] of TEXT_REPLACEMENTS) {
					if (text.indexOf(from) >= 0) {
						text = text.split(from).join(to);
						changed = true;
					}
				}
				if (changed) node.textContent = text;
			}
			return;
		}
		for (let i = 0; i < node.childNodes.length; i++) {
			replaceInVisibleText(node.childNodes[i]);
		}
	}

	function runDomReplacement() {
		try {
			if (document.body) replaceInVisibleText(document.body);
		} catch (_) { /* ignore */ }
	}

	// Run once immediately to prevent flash of "Frappe Framework"
	runDomReplacement();

	// ── MutationObserver (single reactive mechanism) ────────────────
	let observerTimeout;
	let observer;

	function startObserver() {
		if (typeof MutationObserver === "undefined" || !document.body) return;

		observer = new MutationObserver(function () {
			clearTimeout(observerTimeout);
			observerTimeout = setTimeout(runDomReplacement, 80);
		});
		observer.observe(document.body, { childList: true, subtree: true });
	}

	// Clean up on page unload
	window.addEventListener("beforeunload", function () {
		if (observer) {
			observer.disconnect();
			observer = null;
		}
	});

	// ── Frappe Patches (run once after desk bundles load) ────────────
	function runPatches() {
		if (typeof frappe === "undefined" || typeof $ === "undefined") return;

		// 1) Filter boot help items — keep only System Health & Keyboard Shortcuts
		try {
			if (frappe.boot && frappe.boot.standard_help_items) {
				const ALLOWED = ["System Health", "Keyboard Shortcuts"];
				frappe.boot.standard_help_items = frappe.boot.standard_help_items.filter(
					(item) => ALLOWED.includes(item.item_label)
				);
			}
			if (frappe.help) {
				frappe.help.help_links = {};
				frappe.help.add_main_help_links = function () { return; };
			}
		} catch (_) { /* ignore */ }

		// 2) DOM cleanup for dynamic help/support links
		const ALLOWED_LABELS = ["System Health", "Keyboard Shortcuts"];

		const cleanDynamicLinks = () => {
			$(".dropdown-help .dropdown-item, .dropdown-help li").each(function () {
				const $item = $(this);
				const text = $item.text().trim();
				const onclick = $item.attr("onclick") || "";

				if ($item.closest(".dropdown-help").length > 0) {
					if (text && !ALLOWED_LABELS.includes(text)) {
						$item.hide();
					}
				}
				if (text === "About" || onclick.indexOf("show_about") >= 0) {
					$item.hide();
				}
			});

			$('[data-label="Support"], [data-label*="Support"]').each(function () {
				$(this).hide();
			});
		};

		cleanDynamicLinks();
		$(document).on("page-change", cleanDynamicLinks);

		// 3) Theme switcher: "Frappe Light" → "Light"
		try {
			if (frappe.ui.ThemeSwitcher) {
				const proto = frappe.ui.ThemeSwitcher.prototype;
				if (proto.fetch_themes) {
					const _fetch = proto.fetch_themes;
					proto.fetch_themes = function () {
						return _fetch.apply(this, arguments).then(() => {
							const light = this.themes && this.themes.find((t) => t.name === "light");
							if (light) light.label = __("Light");
							return Promise.resolve();
						});
					};
				}
				if (proto.render) {
					const _render = proto.render;
					proto.render = function () {
						_render.apply(this, arguments);
						this.themes.forEach((t) => {
							if (t.name === "light" && t.$html) {
								const $title = t.$html.find(".theme-title");
								if ($title.length) {
									const txt = $title.text();
									if (txt.indexOf("Frappe") >= 0) $title.text(__("Light"));
								}
							}
						});
					};
				}
			}
		} catch (e) {
			console.warn("Possibleworks: Theme switcher patch failed", e);
		}

		// 4) Help sidebar: filter out "About" and "Support"
		try {
			if (frappe.ui.SidebarHeader && frappe.ui.SidebarHeader.prototype.get_help_siblings) {
				const _get = frappe.ui.SidebarHeader.prototype.get_help_siblings;
				frappe.ui.SidebarHeader.prototype.get_help_siblings = function () {
					const items = _get.apply(this, arguments);
					return (items || []).filter((item) => {
						const label = item.label || "";
						return label !== "About" && label.indexOf("Support") === -1;
					});
				};
			}
		} catch (e) {
			console.warn("Possibleworks: Help sidebar patch failed", e);
		}

		// 5) Menu filtering: remove "About" and "Frappe Support" from create_menu
		try {
			if (typeof frappe.ui.create_menu === "function") {
				const _create = frappe.ui.create_menu;
				frappe.ui.create_menu = function (opts) {
					if (opts && opts.menu_items && Array.isArray(opts.menu_items)) {
						opts = {
							...opts,
							menu_items: opts.menu_items.filter((item) => {
								return item.label !== "About" && item.label !== "Frappe Support";
							}),
						};
					}
					return _create.call(this, opts);
				};
			}
		} catch (e) {
			console.warn("Possibleworks: Menu patch failed", e);
		}

		// 6) Page title patch: replace "Frappe Framework" in set_title
		try {
			if (frappe.utils && frappe.utils.set_title) {
				const _set_title = frappe.utils.set_title;
				frappe.utils.set_title = function (title) {
					if (title && typeof title === "string") {
						title = title
							.replace(/Frappe Frameworks/g, BRAND)
							.replace(/Frappe Framework/g, BRAND);
					}
					return _set_title.call(this, title);
				};
			}
		} catch (e) {
			console.warn("Possibleworks: set_title patch failed", e);
		}

		// 7) Router: run DOM replacement on route change
		try {
			if (frappe.router && typeof frappe.router.on === "function") {
				frappe.router.on("change", runDomReplacement);
			}
		} catch (_) { /* ignore */ }
	}

	// ── Bootstrap ───────────────────────────────────────────────────
	function boot() {
		startObserver();
		runPatches();
	}

	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", function () {
			setTimeout(boot, 0);
		});
	} else {
		setTimeout(boot, 0);
	}
})();
