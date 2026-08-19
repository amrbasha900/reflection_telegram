// Copyright (c) 2019, Youssef Restom and contributors
// Copyright (c) 2026, Amr Basha and contributors
// For license information, please see license.txt

frappe.ui.form.on('Telegram User Settings', {
	setup: function (frm) {
		frm.set_query('party', function () {
			return { filters: { name: ['in', ['User', 'Employee', 'Customer', 'Supplier', 'Contact']] } };
		});
	},

	refresh: function (frm) {
		if (frm.is_new()) return;

		frm.dashboard.clear_headline();
		if (frm.doc.telegram_chat_id) {
			frm.dashboard.set_headline(
				__('Linked to chat {0}{1}', [
					frm.doc.telegram_chat_id,
					frm.doc.linked_chat_title ? ` (${frm.doc.linked_chat_title})` : '',
				]),
				'green'
			);
		} else {
			frm.dashboard.set_headline(
				__('Not linked yet. Show the QR code below and ask the recipient to scan it and press START.'),
				'orange'
			);
		}

		frm.add_custom_button(__('Show QR Full Screen'), () => show_qr_dialog(frm));

		if (!frm.doc.telegram_chat_id) {
			frm.add_custom_button(__('Check Link'), () => {
				frm.call({ doc: frm.doc, method: 'check_link', freeze: true }).then((r) => {
					if (r.message && r.message.linked) {
						frappe.show_alert({ message: __('Linked!'), indicator: 'green' });
						frm.reload_doc();
					}
				});
			});
		}

		frm.add_custom_button(
			__('Regenerate QR'),
			() => {
				frappe.confirm(
					__('This issues a new QR code and unlinks the current chat. Continue?'),
					() => {
						frm.call({ doc: frm.doc, method: 'regenerate_qr', freeze: true }).then(() => {
							frm.reload_doc();
						});
					}
				);
			},
			__('Actions')
		);

		if (frm.doc.telegram_chat_id) {
			frm.add_custom_button(
				__('Send Test Message'),
				() => send_test(frm),
				__('Actions')
			);
		}
	},
});

function show_qr_dialog(frm) {
	if (!frm.doc.qr_code) {
		frappe.msgprint(__('No QR code has been generated for this record yet.'));
		return;
	}

	const d = new frappe.ui.Dialog({
		title: __('Scan to link {0}', [frm.doc.telegram_user]),
		size: 'small',
		primary_action_label: __('Print'),
		primary_action: () => window.open(frm.doc.qr_code, '_blank'),
	});

	d.$body.html(`
		<div style="text-align:center; padding: 15px;">
			<img src="${frappe.utils.escape_html(frm.doc.qr_code)}"
			     style="width:100%; max-width:320px; image-rendering: pixelated;">
			<p style="margin-top:15px; font-size: 12px; color: var(--text-muted); word-break: break-all;">
				${frappe.utils.escape_html(frm.doc.deep_link || '')}
			</p>
		</div>
	`);
	d.show();
}

function send_test(frm) {
	frappe.prompt(
		{ fieldname: 'message', fieldtype: 'Small Text', label: __('Message'), reqd: 1, default: __('Test message') },
		(values) => {
			frappe.call({
				method: 'reflection_telegram.api.send_message',
				args: { telegram_user: frm.doc.name, message: values.message },
				freeze: true,
			}).then(() => frappe.show_alert({ message: __('Sent'), indicator: 'green' }));
		},
		__('Send Test Message'),
		__('Send')
	);
}
