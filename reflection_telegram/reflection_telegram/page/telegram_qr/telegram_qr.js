// Copyright (c) 2026, Amr Basha and contributors
// For license information, please see license.txt

frappe.pages['telegram-qr'].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __('Telegram QR Codes'),
		single_column: true,
	});

	new TelegramQRPage(page);
};

const PAGE_SIZES = [12, 24, 48, 96];
const SHEET_LAYOUTS = [
	{ value: 2, label: __('2 per A4 page — large') },
	{ value: 4, label: __('4 per A4 page') },
	{ value: 6, label: __('6 per A4 page') },
	{ value: 8, label: __('8 per A4 page') },
	{ value: 9, label: __('9 per A4 page — recommended') },
	{ value: 12, label: __('12 per A4 page — small') },
];

class TelegramQRPage {
	constructor(page) {
		this.page = page;
		this.state = {
			party_type: null,
			telegram_settings: null,
			search: '',
			link_status: '',
			start: 0,
			page_length: 24,
			is_group_chat: 0,
		};
		this.rows = [];
		this.total = 0;
		this.selected = new Set();
		this.select_all_matching = false;

		this.inject_styles();
		this.$body = $('<div class="tg-qr">').appendTo(this.page.main);
		this.listen_for_progress();
		this.boot();
	}

	// ---------------------------------------------------------------- setup

	inject_styles() {
		if (document.getElementById('tg-qr-styles')) return;

		$(`<style id="tg-qr-styles">
			.tg-qr { padding-bottom: 80px; }
			.tg-qr-chooser { display: grid; gap: 16px; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); margin-top: 8px; }
			.tg-qr-type {
				background: var(--card-bg); border: 1px solid var(--border-color); border-radius: var(--border-radius-lg);
				padding: 28px 20px; text-align: center; cursor: pointer; transition: all .15s ease;
			}
			.tg-qr-type:hover { border-color: var(--primary); transform: translateY(-2px); box-shadow: var(--shadow-md); }
			.tg-qr-type .tg-icon { font-size: 30px; line-height: 1; margin-bottom: 12px; }
			.tg-qr-type .tg-name { font-size: var(--text-lg); font-weight: 600; }
			.tg-qr-type .tg-count { color: var(--text-muted); font-size: var(--text-sm); margin-top: 4px; }

			.tg-qr-toolbar {
				position: sticky; top: 0; z-index: 5; background: var(--fg-color);
				display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
				padding: 10px 0; border-bottom: 1px solid var(--border-color); margin-bottom: 14px;
			}
			.tg-qr-toolbar .tg-spacer { flex: 1 1 auto; }
			.tg-qr-search { min-width: 220px; }

			.tg-qr-banner {
				background: var(--bg-light-gray); border: 1px solid var(--border-color);
				border-radius: var(--border-radius-md); padding: 8px 12px; margin-bottom: 12px;
				display: flex; gap: 10px; align-items: center; font-size: var(--text-sm);
			}

			.tg-qr-grid { display: grid; gap: 12px; grid-template-columns: repeat(auto-fill, minmax(190px, 1fr)); }
			.tg-qr-tile {
				background: var(--card-bg); border: 2px solid var(--border-color); border-radius: var(--border-radius-lg);
				padding: 12px; cursor: pointer; transition: border-color .12s ease, box-shadow .12s ease;
				display: flex; flex-direction: column; gap: 8px;
			}
			.tg-qr-tile:hover { box-shadow: var(--shadow-md); }
			.tg-qr-tile.is-selected { border-color: var(--primary); }
			.tg-qr-tile .tg-head { display: flex; align-items: flex-start; gap: 8px; }
			.tg-qr-tile .tg-title { font-weight: 600; font-size: var(--text-md); line-height: 1.3; word-break: break-word; }
			.tg-qr-tile .tg-sub { color: var(--text-muted); font-size: var(--text-xs); }
			.tg-qr-thumb {
				aspect-ratio: 1; display: flex; align-items: center; justify-content: center;
				background: var(--bg-light-gray); border-radius: var(--border-radius-md); overflow: hidden;
			}
			.tg-qr-thumb img { width: 100%; height: 100%; object-fit: contain; image-rendering: pixelated; background: #fff; }
			.tg-qr-thumb .tg-empty { color: var(--text-muted); font-size: var(--text-xs); text-align: center; padding: 10px; }

			.tg-qr-pager { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; justify-content: center; margin-top: 22px; }
			.tg-qr-pager .btn { min-width: 34px; }
			.tg-qr-pager .tg-gap { color: var(--text-muted); padding: 0 2px; }
			.tg-qr-summary { text-align: center; color: var(--text-muted); font-size: var(--text-sm); margin-top: 8px; }

			.tg-qr-empty { text-align: center; padding: 60px 20px; color: var(--text-muted); }
		</style>`).appendTo(document.head);
	}

	boot() {
		frappe.db.get_list('Telegram Settings', { fields: ['name'], limit: 2 }).then((settings) => {
			if (!settings || !settings.length) {
				this.$body.html(`
					<div class="tg-qr-empty">
						<p>${__('Create a Telegram Settings record with your bot token first.')}</p>
						<button class="btn btn-primary btn-sm tg-new-settings">${__('New Telegram Settings')}</button>
					</div>`);
				this.$body.find('.tg-new-settings').on('click', () => frappe.new_doc('Telegram Settings'));
				return;
			}

			this.all_settings = settings.map((s) => s.name);
			this.state.telegram_settings = this.all_settings[0];
			this.render_chooser();
		});
	}

	listen_for_progress() {
		frappe.realtime.on('telegram_qr_progress', (data) => {
			if (data.finished) {
				frappe.show_alert(
					{
						message: __('{0} QR codes ready, {1} failed', [data.done, data.failed]),
						indicator: data.failed ? 'orange' : 'green',
					},
					7
				);
				this.load();
			} else {
				frappe.show_progress(
					__('Generating QR codes'),
					data.done + data.failed,
					data.total,
					__('{0} of {1}', [data.done + data.failed, data.total])
				);
			}
		});
	}

	// ------------------------------------------------------------ step one

	render_chooser() {
		this.page.clear_primary_action();
		this.page.clear_menu();
		this.page.set_title(__('Telegram QR Codes'));

		this.$body.html(`
			<p class="text-muted" style="margin-top:12px">
				${__('Choose who you are creating QR codes for.')}
			</p>
			<div class="tg-qr-chooser"><div class="text-muted">${__('Loading...')}</div></div>
		`);

		frappe.call('reflection_telegram.qr_page.get_party_types').then((r) => {
			const icons = { Supplier: '🚚', Customer: '🧾', Employee: '👤', Contact: '📇', User: '🔑' };
			const html = (r.message || [])
				.map(
					(t) => `
					<div class="tg-qr-type" data-type="${frappe.utils.escape_html(t.party_type)}">
						<div class="tg-icon">${icons[t.party_type] || '📄'}</div>
						<div class="tg-name">${__(t.party_type)}</div>
						<div class="tg-count">${__('{0} records', [frappe.format(t.count, { fieldtype: 'Int' })])}</div>
					</div>`
				)
				.join('');

			this.$body.find('.tg-qr-chooser').html(html || `<div class="text-muted">${__('Nothing available')}</div>`);
			this.$body.find('.tg-qr-type').on('click', (e) => {
				this.state.party_type = $(e.currentTarget).data('type');
				this.reset_selection();
				this.state.start = 0;
				this.render_list_shell();
				this.load();
			});
		});
	}

	// ------------------------------------------------------------ step two

	render_list_shell() {
		this.page.set_title(__('QR Codes — {0}', [__(this.state.party_type)]));
		this.page.set_primary_action(__('Generate QR'), () => this.generate());
		this.page.clear_menu();
		this.page.add_menu_item(__('Change Party Type'), () => this.render_chooser());
		this.page.add_menu_item(__('Print...'), () => this.print_dialog());

		const settings_picker =
			this.all_settings.length > 1
				? `<select class="form-control input-xs tg-settings" style="width:auto">
						${this.all_settings
							.map(
								(s) =>
									`<option value="${frappe.utils.escape_html(s)}" ${
										s === this.state.telegram_settings ? 'selected' : ''
									}>${frappe.utils.escape_html(s)}</option>`
							)
							.join('')}
				   </select>`
				: '';

		this.$body.html(`
			<div class="tg-qr-toolbar">
				<input type="search" class="form-control input-xs tg-qr-search"
				       placeholder="${__('Search {0}...', [__(this.state.party_type)])}">
				<select class="form-control input-xs tg-status" style="width:auto">
					<option value="">${__('All')}</option>
					<option value="unlinked">${__('Not linked')}</option>
					<option value="linked">${__('Linked')}</option>
					<option value="no_qr">${__('No QR yet')}</option>
				</select>
				${settings_picker}
				<label class="text-muted" style="margin:0; display:flex; align-items:center; gap:5px; font-weight:normal">
					<input type="checkbox" class="tg-group"> ${__('Group chat')}
				</label>
				<span class="tg-spacer"></span>
				<button class="btn btn-xs btn-default tg-select-page">${__('Select Page')}</button>
				<button class="btn btn-xs btn-default tg-clear">${__('Clear')}</button>
				<select class="form-control input-xs tg-page-length" style="width:auto">
					${PAGE_SIZES.map(
						(n) => `<option value="${n}" ${n === this.state.page_length ? 'selected' : ''}>${n} / ${__('page')}</option>`
					).join('')}
				</select>
			</div>
			<div class="tg-qr-banner" style="display:none"></div>
			<div class="tg-qr-body"></div>
			<div class="tg-qr-pager"></div>
			<div class="tg-qr-summary"></div>
		`);

		this.$search = this.$body.find('.tg-qr-search');
		this.$banner = this.$body.find('.tg-qr-banner');
		this.$list = this.$body.find('.tg-qr-body');
		this.$pager = this.$body.find('.tg-qr-pager');
		this.$summary = this.$body.find('.tg-qr-summary');

		this.$search.on(
			'input',
			frappe.utils.debounce(() => {
				this.state.search = this.$search.val();
				this.state.start = 0;
				this.reset_selection();
				this.load();
			}, 350)
		);

		this.$body.find('.tg-status').on('change', (e) => {
			this.state.link_status = e.target.value;
			this.state.start = 0;
			this.reset_selection();
			this.load();
		});

		this.$body.find('.tg-settings').on('change', (e) => {
			this.state.telegram_settings = e.target.value;
			this.state.start = 0;
			this.reset_selection();
			this.load();
		});

		this.$body.find('.tg-page-length').on('change', (e) => {
			this.state.page_length = cint(e.target.value);
			this.state.start = 0;
			this.load();
		});

		this.$body.find('.tg-group').on('change', (e) => {
			this.state.is_group_chat = e.target.checked ? 1 : 0;
		});

		this.$body.find('.tg-select-page').on('click', () => {
			this.rows.forEach((r) => this.selected.add(r.party));
			this.render_rows();
		});

		this.$body.find('.tg-clear').on('click', () => {
			this.reset_selection();
			this.render_rows();
		});
	}

	reset_selection() {
		this.selected.clear();
		this.select_all_matching = false;
	}

	load() {
		frappe
			.call({
				method: 'reflection_telegram.qr_page.get_parties',
				args: {
					party_type: this.state.party_type,
					telegram_settings: this.state.telegram_settings,
					search: this.state.search,
					link_status: this.state.link_status,
					start: this.state.start,
					page_length: this.state.page_length,
				},
			})
			.then((r) => {
				const res = r.message || {};
				this.rows = res.rows || [];
				this.total = res.total || 0;
				this.render_rows();
				this.render_pager();
			});
	}

	render_rows() {
		if (!this.rows.length) {
			this.$list.html(`<div class="tg-qr-empty">${__('No records match this filter.')}</div>`);
		} else {
			this.$list.html(
				`<div class="tg-qr-grid">${this.rows.map((row) => this.tile(row)).join('')}</div>`
			);

			this.$list.find('.tg-qr-tile').on('click', (e) => {
				const party = $(e.currentTarget).data('party') + '';
				this.selected.has(party) ? this.selected.delete(party) : this.selected.add(party);
				this.select_all_matching = false;
				this.render_rows();
			});
		}

		this.render_banner();
	}

	tile(row) {
		const selected = this.selected.has(row.party);
		const status = row.linked
			? `<span class="indicator-pill green">${__('Linked')}</span>`
			: row.telegram_user && row.qr_code
			? `<span class="indicator-pill orange">${__('Awaiting scan')}</span>`
			: `<span class="indicator-pill gray">${__('No QR yet')}</span>`;

		const thumb = row.qr_code
			? `<img src="${frappe.utils.escape_html(row.qr_code)}" loading="lazy" alt="QR">`
			: `<div class="tg-empty">${__('Not generated')}</div>`;

		return `
			<div class="tg-qr-tile ${selected ? 'is-selected' : ''}" data-party="${frappe.utils.escape_html(row.party)}">
				<div class="tg-head">
					<input type="checkbox" ${selected ? 'checked' : ''} style="pointer-events:none; margin-top:3px">
					<div style="min-width:0; flex:1">
						<div class="tg-title">${frappe.utils.escape_html(row.party_name || row.party)}</div>
						<div class="tg-sub">${frappe.utils.escape_html(row.party)}</div>
					</div>
				</div>
				<div class="tg-qr-thumb">${thumb}</div>
				<div>${status}</div>
			</div>`;
	}

	render_banner() {
		const page_count = this.rows.length;
		const all_on_page_selected = page_count && this.rows.every((r) => this.selected.has(r.party));

		if (this.select_all_matching) {
			this.$banner
				.show()
				.html(
					`<b>${__('All {0} matching records are selected.', [this.total])}</b>
					 <a class="tg-unselect-all">${__('Clear selection')}</a>`
				);
			this.$banner.find('.tg-unselect-all').on('click', () => {
				this.reset_selection();
				this.render_rows();
			});
		} else if (all_on_page_selected && this.total > page_count) {
			this.$banner
				.show()
				.html(
					`${__('All {0} on this page are selected.', [page_count])}
					 <a class="tg-select-all">${__('Select all {0} matching', [this.total])}</a>`
				);
			this.$banner.find('.tg-select-all').on('click', () => {
				this.select_all_matching = true;
				this.render_rows();
			});
		} else if (this.selected.size) {
			this.$banner.show().text(__('{0} selected', [this.selected.size]));
		} else {
			this.$banner.hide().empty();
		}
	}

	render_pager() {
		const pages = Math.ceil(this.total / this.state.page_length) || 1;
		const current = Math.floor(this.state.start / this.state.page_length) + 1;

		const from = this.total ? this.state.start + 1 : 0;
		const to = Math.min(this.state.start + this.state.page_length, this.total);
		this.$summary.text(__('Showing {0}–{1} of {2}', [from, to, this.total]));

		if (pages <= 1) {
			this.$pager.empty();
			return;
		}

		const button = (label, page, disabled, active) =>
			`<button class="btn btn-xs ${active ? 'btn-primary' : 'btn-default'} tg-page"
			         data-page="${page}" ${disabled ? 'disabled' : ''}>${label}</button>`;

		let html = button('‹', current - 1, current === 1, false);

		// First, last, and a window around the current page -- enough to jump
		// around 100+ pages without rendering 100 buttons.
		const window_pages = new Set([1, pages, current, current - 1, current + 1, current - 2, current + 2]);
		const visible = [...window_pages].filter((p) => p >= 1 && p <= pages).sort((a, b) => a - b);

		let previous = 0;
		visible.forEach((p) => {
			if (p - previous > 1) html += `<span class="tg-gap">…</span>`;
			html += button(p, p, false, p === current);
			previous = p;
		});

		html += button('›', current + 1, current === pages, false);
		this.$pager.html(html);

		this.$pager.find('.tg-page').on('click', (e) => {
			const page = cint($(e.currentTarget).data('page'));
			if (page < 1 || page > pages) return;
			this.state.start = (page - 1) * this.state.page_length;
			this.load();
			frappe.utils.scroll_to(this.$body);
		});
	}

	// ------------------------------------------------------------- actions

	get selection_args() {
		if (this.select_all_matching) {
			return {
				select_all: 1,
				search: this.state.search,
				link_status: this.state.link_status,
			};
		}
		return { parties: [...this.selected] };
	}

	generate() {
		if (!this.select_all_matching && !this.selected.size) {
			frappe.msgprint(__('Select at least one record first.'));
			return;
		}

		const count = this.select_all_matching ? this.total : this.selected.size;
		const run = () => {
			frappe
				.call({
					method: 'reflection_telegram.qr_page.generate',
					args: {
						party_type: this.state.party_type,
						telegram_settings: this.state.telegram_settings,
						is_group_chat: this.state.is_group_chat,
						...this.selection_args,
					},
					freeze: count <= 25,
					freeze_message: __('Generating QR codes...'),
				})
				.then((r) => {
					const res = r.message || {};
					if (res.queued) {
						frappe.show_alert(
							{ message: __('Generating {0} QR codes in the background...', [res.total]), indicator: 'blue' },
							7
						);
						return;
					}
					if (res.errors && res.errors.length) {
						frappe.msgprint({
							title: __('Some records failed'),
							indicator: 'orange',
							message: res.errors
								.map((e) => `${frappe.utils.escape_html(e.party)}: ${frappe.utils.escape_html(e.error)}`)
								.join('<br>'),
						});
					} else {
						frappe.show_alert({ message: __('{0} QR codes ready', [res.total]), indicator: 'green' });
					}
					this.load();
				});
		};

		if (count > 25) {
			frappe.confirm(__('Generate QR codes for {0} records? This runs in the background.', [count]), run);
		} else {
			run();
		}
	}

	print_dialog() {
		if (!this.select_all_matching && !this.selected.size) {
			frappe.msgprint(__('Select the records you want to print first.'));
			return;
		}

		const count = this.select_all_matching ? this.total : this.selected.size;

		const d = new frappe.ui.Dialog({
			title: __('Print QR Codes'),
			fields: [
				{
					fieldname: 'per_page',
					label: __('Cards per A4 page'),
					fieldtype: 'Select',
					options: SHEET_LAYOUTS.map((l) => ({ value: l.value, label: l.label })),
					default: 9,
					reqd: 1,
				},
				{
					fieldname: 'info',
					fieldtype: 'HTML',
					options: `<p class="text-muted">${__('{0} records selected. Only records that already have a QR code are printed.', [count])}</p>`,
				},
			],
			primary_action_label: __('Print'),
			primary_action: (values) => {
				d.hide();
				this.print(cint(values.per_page));
			},
		});

		d.show();
	}

	print(per_page) {
		// Opened on the click so the browser attributes the popup to a user action.
		const win = window.open('', '_blank');

		frappe
			.call({
				method: 'reflection_telegram.qr_page.print_html',
				args: {
					party_type: this.state.party_type,
					telegram_settings: this.state.telegram_settings,
					per_page: per_page,
					// Party names, not linking-record names: the selection can span
					// pages the browser no longer holds rows for.
					...this.selection_args,
				},
				freeze: true,
				freeze_message: __('Building the print sheet...'),
			})
			.then((r) => {
				if (!win) {
					frappe.msgprint(__('Allow pop-ups for this site to print.'));
					return;
				}
				win.document.write(r.message);
				win.document.close();
			})
			.catch(() => win && win.close());
	}
}
