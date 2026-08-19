// Copyright (c) 2026, Amr Basha and contributors
// For license information, please see license.txt

frappe.ui.form.on('Telegram Broadcast', {
	refresh: function (frm) {
		if (frm.is_new()) return;

		frm.add_custom_button(__('Refresh Counts'), () => {
			frm.call({ doc: frm.doc, method: 'refresh_counts', freeze: true }).then(() => frm.reload_doc());
		});

		if (frm.doc.failed) {
			frm.add_custom_button(__('Retry Failed ({0})', [frm.doc.failed]), () => {
				frappe.confirm(__('Requeue {0} failed messages?', [frm.doc.failed]), () => {
					frm.call({ doc: frm.doc, method: 'retry_failed', freeze: true }).then(() => frm.reload_doc());
				});
			}).addClass('btn-primary');
		}

		if (['Queued', 'In Progress'].includes(frm.doc.status)) {
			frm.add_custom_button(__('Cancel Pending'), () => {
				frappe.confirm(__('Stop the messages that have not gone out yet?'), () => {
					frm.call({ doc: frm.doc, method: 'cancel_pending', freeze: true }).then(() => frm.reload_doc());
				});
			});
		}

		frm.add_custom_button(__('View Messages'), () => {
			frappe.set_route('List', 'Telegram Outbox', { broadcast: frm.doc.name });
		});
	},
});
