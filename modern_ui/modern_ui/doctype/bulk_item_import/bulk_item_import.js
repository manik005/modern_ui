frappe.ui.form.on("Bulk Item Import", {
  refresh(frm) {
    if (frm.is_new()) return;

    frm.add_custom_button("Import Now", () => {
      frm.call("import_csv").then((r) => {
        if (r.message) {
          const summary = [
            `Created Items: ${r.message.created_items}`,
            `Updated Items: ${r.message.updated_items}`,
            `Created Prices: ${r.message.created_prices}`,
            `Updated Prices: ${r.message.updated_prices}`,
            `Stock Reconciliations: ${r.message.stock_reconciliations}`,
          ].join("\n");
          frappe.msgprint({
            title: "Import Complete",
            message: summary,
            indicator: "green",
          });
        }
        frm.reload_doc();
      });
    });
  },
});
