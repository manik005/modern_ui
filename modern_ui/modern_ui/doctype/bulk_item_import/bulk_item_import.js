frappe.ui.form.on("Bulk Item Import", {
  refresh(frm) {
    if (frm.is_new()) return;

    frm.add_custom_button("Detect Columns", () => {
      frm.call("detect_columns").then((r) => {
        if (r.message) {
          const columns = r.message.columns;
          frappe.msgprint({
            title: "CSV Columns Detected",
            message: `<p><strong>Columns found:</strong></p><pre>${columns.join(
              "\n"
            )}</pre>
          <p>Map these columns to Item, Stock, and Pricing fields.</p>`,
            indicator: "blue",
          });
        }
      });
    });

    frm.add_custom_button("Import Now", () => {
      if (!frm.doc.csv_file) {
        frappe.msgprint("Please attach a CSV file.");
        return;
      }

      frappe.confirm(
        "This will create/update Items, Prices, and Stock Reconciliations. Continue?",
        () => {
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
        }
      );
    });
  },

  csv_file(frm) {
    if (frm.doc.csv_file) {
      frm.call("detect_columns").then((r) => {
        if (r.message) {
          const columns = r.message.columns.join(", ");
          frappe.toast({
            title: "Columns Detected",
            message: columns,
            indicator: "blue",
          });
        }
      });
    }
  },
});

