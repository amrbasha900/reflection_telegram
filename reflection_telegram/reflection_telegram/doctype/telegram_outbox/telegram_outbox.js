// Copyright (c) 2026, Amr Basha and contributors
// For license information, please see license.txt

frappe.ui.form.on('Telegram Outbox', {
	refresh: function (frm) {
		if (frm.is_new()) return;

		const indicators = { Sent: 'green', Failed: 'red', Queued: 'orange', Sending: 'blue', Cancelled: 'gray' };
		frm.dashboard.clear_headline();
		frm.dashboard.set_headline(
			frm.doc.error
				? __('{0} after {1} attempt(s): {2}', [frm.doc.status, frm.doc.attempts || 0, frm.doc.error])
				: __('{0} after {1} attempt(s)', [frm.doc.status, frm.doc.attempts || 0]),
			indicators[frm.doc.status] || 'gray'
		);

		if (['Failed', 'Cancelled'].includes(frm.doc.status)) {
			frm.add_custom_button(__('Retry'), () => {
				frm.call({ doc: frm.doc, method: 'retry', freeze: true }).then(() => frm.reload_doc());
			}).addClass('btn-primary');
		}
	},
});
