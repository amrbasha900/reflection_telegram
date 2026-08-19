// Copyright (c) 2019, Youssef Restom and contributors
// Copyright (c) 2026, Amr Basha and contributors
// For license information, please see license.txt

frappe.ui.form.on('Telegram Settings', {
	refresh: function (frm) {
		if (frm.is_new()) return;

		const registered = frm.doc.webhook_status === 'Registered';

		frm.dashboard.clear_headline();
		frm.dashboard.set_headline(
			registered
				? __('Webhook registered. Scans link instantly.')
				: __('No webhook. Linking relies on polling or the manual Check Link button.'),
			registered ? 'green' : 'orange'
		);

		frm.add_custom_button(__('Check Bot'), () => {
			frm.call({ method: 'reflection_telegram.webhook.status', args: { telegram_settings: frm.doc.name }, freeze: true })
				.then((r) => {
					const s = r.message || {};
					frappe.msgprint({
						title: __('Bot Status'),
						indicator: s.registered ? 'green' : 'orange',
						message: `
							<table class="table table-bordered" style="margin:0">
								<tr><td>${__('Bot')}</td><td>@${frappe.utils.escape_html(s.bot_username || '')}</td></tr>
								<tr><td>${__('Webhook')}</td><td style="word-break:break-all">${frappe.utils.escape_html(s.webhook_url || __('Not registered'))}</td></tr>
								<tr><td>${__('Pending Updates')}</td><td>${s.pending_update_count || 0}</td></tr>
								<tr><td>${__('Last Error')}</td><td>${frappe.utils.escape_html(s.last_error_message || '-')}</td></tr>
							</table>`,
					});
					frm.reload_doc();
				});
		});

		if (registered) {
			frm.add_custom_button(__('Remove Webhook'), () => {
				frappe.confirm(
					__('Linking will stop being instant. Enable polling as a fallback if you rely on QR onboarding. Continue?'),
					() => call_webhook(frm, 'unregister')
				);
			}, __('Webhook'));
		} else {
			frm.add_custom_button(__('Register Webhook'), () => call_webhook(frm, 'register'), __('Webhook'));
		}

		frm.add_custom_button(__('Telegram QR Codes'), () => frappe.set_route('telegram-qr'));
	},

	enable_polling: function (frm) {
		if (frm.doc.enable_polling && frm.doc.webhook_status === 'Registered') {
			frappe.msgprint(
				__('Telegram will not return updates through getUpdates while a webhook is registered, so polling stays idle until the webhook is removed.')
			);
		}
	},
});

function call_webhook(frm, action) {
	frm.call({
		method: `reflection_telegram.webhook.${action}`,
		args: { telegram_settings: frm.doc.name },
		freeze: true,
	}).then(() => frm.reload_doc());
}
