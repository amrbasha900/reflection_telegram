frappe.listview_settings['Telegram Outbox'] = {
	add_fields: ['status', 'attempts'],
	get_indicator: function (doc) {
		const map = { Sent: 'green', Failed: 'red', Queued: 'orange', Sending: 'blue', Cancelled: 'gray' };
		return [__(doc.status), map[doc.status] || 'gray', 'status,=,' + doc.status];
	},
	onload: function (listview) {
		listview.page.add_actions_menu_item(__('Retry'), () => {
			const names = listview.get_checked_items(true);
			if (!names.length) return;

			frappe.call({
				method: 'reflection_telegram.reflection_telegram.doctype.telegram_outbox.telegram_outbox.retry_messages',
				args: { names: names },
				freeze: true,
			}).then((r) => {
				frappe.show_alert({ message: __('{0} messages requeued', [r.message || 0]), indicator: 'green' });
				listview.refresh();
			});
		});
	},
};
