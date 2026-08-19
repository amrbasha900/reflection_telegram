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

class TelegramQRPage {
	constructor(page) {
		this.page = page;
		this.rows = [];
		this.selected = new Set();

		this.make_filters();
		this.make_body();
		this.make_actions();
		this.load_settings();
	}

	make_filters() {
		this.settings_field = this.page.add_field({
			fieldname: 'telegram_settings',
			label: __('Telegram Settings'),
			fieldtype: 'Link',
			options: 'Telegram Settings',
			reqd: 1,
			change: () => this.refresh(),
		});

		this.party_type_field = this.page.add_field({
			fieldname: 'party_type',
			label: __('Party Type'),
			fieldtype: 'Select',
			options: ['Supplier', 'Customer', 'Employee', 'Contact', 'User'],
			default: 'Supplier',
			change: () => this.refresh(),
		});

		this.search_field = this.page.add_field({
			fieldname: 'search',
			label: __('Search'),
			fieldtype: 'Data',
			change: () => this.refresh(),
		});

		this.only_unlinked_field = this.page.add_field({
			fieldname: 'only_unlinked',
			label: __('Only Unlinked'),
			fieldtype: 'Check',
			change: () => this.refresh(),
		});

		this.group_field = this.page.add_field({
			fieldname: 'is_group_chat',
			label: __('Group Chat'),
			fieldtype: 'Check',
			description: __('Generate links that add the bot to a group instead of a private chat'),
		});
	}

	make_body() {
		this.page.main.html(`
			<div class="telegram-qr-page">
				<div class="qr-toolbar" style="display:flex; gap:8px; align-items:center; margin-bottom:12px;">
					<button class="btn btn-xs btn-default qr-select-all">${__('Select All')}</button>
					<button class="btn btn-xs btn-default qr-select-none">${__('Clear')}</button>
					<span class="qr-count text-muted" style="margin-inline-start:auto"></span>
				</div>
				<div class="qr-results"></div>
			</div>
		`);

		this.$results = this.page.main.find('.qr-results');
		this.$count = this.page.main.find('.qr-count');

		this.page.main.find('.qr-select-all').on('click', () => {
			this.rows.forEach((r) => this.selected.add(r.party));
			this.render();
		});
		this.page.main.find('.qr-select-none').on('click', () => {
			this.selected.clear();
			this.render();
		});
	}

	make_actions() {
		this.page.set_primary_action(__('Generate QR'), () => this.generate());
		this.page.add_button(__('Print Selected'), () => this.print_selected());
		this.page.add_menu_item(__('Print One Per Page'), () => this.print_selected(1));
	}

	load_settings() {
		frappe.db.get_list('Telegram Settings', { fields: ['name'], limit: 1 }).then((r) => {
			if (r && r.length) {
				this.settings_field.set_value(r[0].name);
			} else {
				this.$results.html(
					`<div class="text-muted" style="padding:40px; text-align:center">
						${__('Create a Telegram Settings record with your bot token first.')}
					</div>`
				);
			}
		});
	}

	get values() {
		return {
			telegram_settings: this.settings_field.get_value(),
			party_type: this.party_type_field.get_value(),
			search: this.search_field.get_value(),
			only_unlinked: this.only_unlinked_field.get_value() ? 1 : 0,
			is_group_chat: this.group_field.get_value() ? 1 : 0,
		};
	}

	refresh() {
		const v = this.values;
		if (!v.telegram_settings || !v.party_type) return;

		// Selections are per party type, so switching type must not carry them over.
		this.selected.clear();

		frappe
			.call({
				method: 'reflection_telegram.qr_page.get_parties',
				args: {
					party_type: v.party_type,
					telegram_settings: v.telegram_settings,
					search: v.search,
					only_unlinked: v.only_unlinked,
				},
			})
			.then((r) => {
				this.rows = r.message || [];
				this.render();
			});
	}

	render() {
		if (!this.rows.length) {
			this.$results.html(
				`<div class="text-muted" style="padding:40px; text-align:center">${__('No records found')}</div>`
			);
			this.$count.text('');
			return;
		}

		const cards = this.rows.map((row) => this.card_html(row)).join('');
		this.$results.html(
			`<div style="display:grid; grid-template-columns: repeat(auto-fill, minmax(240px, 1fr)); gap:12px">${cards}</div>`
		);

		this.$count.text(__('{0} selected of {1}', [this.selected.size, this.rows.length]));

		this.$results.find('.qr-tile').on('click', (e) => {
			if ($(e.target).is('a, img')) return;
			const party = $(e.currentTarget).data('party');
			this.selected.has(party) ? this.selected.delete(party) : this.selected.add(party);
			this.render();
		});
	}

	card_html(row) {
		const checked = this.selected.has(row.party);
		const status = row.linked
			? `<span class="indicator-pill green">${__('Linked')}</span>`
			: row.telegram_user
			? `<span class="indicator-pill orange">${__('Awaiting Scan')}</span>`
			: `<span class="indicator-pill gray">${__('No QR Yet')}</span>`;

		const image = row.qr_code
			? `<img src="${frappe.utils.escape_html(row.qr_code)}" style="width:120px; height:120px; image-rendering:pixelated">`
			: `<div style="width:120px;height:120px;display:flex;align-items:center;justify-content:center;
			        border:1px dashed var(--border-color); border-radius:6px; color:var(--text-muted); font-size:11px">
			        ${__('Not generated')}</div>`;

		return `
			<div class="qr-tile frappe-card" data-party="${frappe.utils.escape_html(row.party)}"
				style="padding:12px; cursor:pointer; border:2px solid ${checked ? 'var(--primary)' : 'transparent'}">
				<div style="display:flex; align-items:center; gap:8px; margin-bottom:8px">
					<input type="checkbox" ${checked ? 'checked' : ''} style="pointer-events:none">
					<strong style="flex:1; overflow:hidden; text-overflow:ellipsis; white-space:nowrap">
						${frappe.utils.escape_html(row.party_name || row.party)}
					</strong>
				</div>
				<div class="text-muted" style="font-size:11px; margin-bottom:8px">
					${frappe.utils.escape_html(row.party)}
				</div>
				<div style="text-align:center">${image}</div>
				<div style="margin-top:8px; text-align:center">${status}</div>
			</div>`;
	}

	generate() {
		const v = this.values;
		const parties = [...this.selected];

		if (!parties.length) {
			frappe.msgprint(__('Select at least one record first.'));
			return;
		}

		frappe
			.call({
				method: 'reflection_telegram.qr_page.generate',
				args: {
					party_type: v.party_type,
					parties: parties,
					telegram_settings: v.telegram_settings,
					is_group_chat: v.is_group_chat,
				},
				freeze: true,
				freeze_message: __('Generating QR codes...'),
			})
			.then((r) => {
				const res = r.message || {};
				if (res.errors && res.errors.length) {
					frappe.msgprint({
						title: __('Some records failed'),
						indicator: 'orange',
						message: res.errors
							.map((e) => `${frappe.utils.escape_html(e.party)}: ${frappe.utils.escape_html(e.error)}`)
							.join('<br>'),
					});
				} else {
					frappe.show_alert({
						message: __('{0} QR codes ready', [(res.generated || []).length]),
						indicator: 'green',
					});
				}
				this.refresh();
			});
	}

	print_selected(columns) {
		const users = this.rows
			.filter((r) => this.selected.has(r.party) && r.telegram_user && r.qr_code)
			.map((r) => r.telegram_user);

		if (!users.length) {
			frappe.msgprint(__('Select records that already have a QR code, or generate them first.'));
			return;
		}

		// Opened before the call so the browser attributes the popup to the click.
		const win = window.open('', '_blank');

		frappe
			.call({
				method: 'reflection_telegram.qr_page.print_html',
				args: { telegram_users: users, columns: columns || 2 },
			})
			.then((r) => {
				if (!win) {
					frappe.msgprint(__('Allow pop-ups for this site to print.'));
					return;
				}
				win.document.write(r.message);
				win.document.close();
			});
	}
}
