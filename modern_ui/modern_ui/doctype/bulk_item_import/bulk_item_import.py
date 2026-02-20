import csv
import io
from collections import defaultdict

import frappe
from frappe.model.document import Document
from frappe.utils import cint, flt, nowdate, nowtime


class BulkItemImport(Document):
    def before_insert(self):
        """Pre-populate column mapping from CSV if available"""
        pass

    @frappe.whitelist()
    def detect_columns(self):
        """Detect CSV columns for user mapping"""
        if not self.csv_file:
            frappe.throw("Please attach a CSV file first.")

        file_doc = frappe.get_doc("File", {"file_url": self.csv_file})
        file_path = file_doc.get_full_path()

        try:
            with open(file_path, "r", encoding="utf-8-sig") as f:
                reader = csv.reader(f)
                headers = next(reader)
                return {"columns": headers}
        except Exception as e:
            frappe.throw(f"Error reading CSV: {str(e)}")

    @frappe.whitelist()
    def import_csv(self):
        self.check_permission("write")

        if not self.csv_file:
            frappe.throw("Please attach a CSV file before importing.")

        file_doc = frappe.get_doc("File", {"file_url": self.csv_file})
        file_path = file_doc.get_full_path()

        with open(file_path, "r", encoding="utf-8-sig") as f:
            content = f.read()

        delimiter = (self.delimiter or ",").strip() or ","
        reader = csv.DictReader(io.StringIO(content), delimiter=delimiter)

        created_items = 0
        updated_items = 0
        created_prices = 0
        updated_prices = 0
        stock_recos = 0

        errors = []
        log_lines = []

        stock_map = defaultdict(dict)
        stock_seen = set()

        default_company = self.default_company or frappe.db.get_single_value(
            "Global Defaults", "default_company"
        )
        default_currency = self.default_currency or frappe.db.get_single_value(
            "Global Defaults", "default_currency"
        )
        difference_account = self.difference_account
        account_details = None

        if not difference_account and not self.skip_stock_reconciliation:
            frappe.throw(
                "Difference Account is required for Stock Reconciliation. "
                "Please set a valid Asset/Liability account."
            )

        # Validate difference account type
        if difference_account:
            account_details = frappe.db.get_value(
                "Account",
                difference_account,
                ["account_type", "root_type", "is_group", "company"],
                as_dict=True,
            )
            if not account_details:
                frappe.throw(f"Account {difference_account} does not exist.")

            if not default_company:
                default_company = account_details.company

            if account_details.is_group:
                frappe.throw(
                    f"Account '{difference_account}' is a Group account. "
                    "Please select a Ledger account (not a group)."
                )
            if account_details.root_type not in ["Asset", "Liability"]:
                frappe.throw(
                    f"Account '{difference_account}' is of type '{account_details.root_type}'. "
                    "Please select an Asset or Liability account."
                )
            if default_company and account_details.company != default_company:
                frappe.throw(
                    f"Account '{difference_account}' belongs to company '{account_details.company}'. "
                    f"Please select an account from company '{default_company}'."
                )

        for row_idx, row in enumerate(reader, start=2):
            normalized = {_normalize_header(k): (v or "").strip() for k, v in row.items()}

            item_code = _get_value(
                normalized,
                "item_code",
                "itemcode",
                "sku",
            )
            item_name = _get_value(normalized, "item_name", "itemname", "item")
            item_group = _get_value(normalized, "item_group", "itemgroup", "group")
            stock_uom = _get_value(normalized, "stock_uom", "uom", "stockuom")

            if not item_code:
                errors.append(f"Row {row_idx}: Missing Item Code")
                continue

            item_exists = frappe.db.exists("Item", item_code)

            if not item_exists and not item_name:
                errors.append(f"Row {row_idx}: Item Name required for new Item {item_code}")
                continue

            if not item_exists and not item_group:
                errors.append(f"Row {row_idx}: Item Group required for new Item {item_code}")
                continue

            if not item_exists and not stock_uom:
                errors.append(f"Row {row_idx}: Stock UOM required for new Item {item_code}")
                continue

            if not self.dry_run:
                if item_exists:
                    item = frappe.get_doc("Item", item_code)
                    if self.update_existing:
                        _apply_item_fields(item, normalized, item_name, item_group, stock_uom)
                        item.save(ignore_permissions=True)
                        updated_items += 1
                else:
                    item = frappe.new_doc("Item")
                    item.item_code = item_code
                    _apply_item_fields(item, normalized, item_name, item_group, stock_uom)
                    item.insert(ignore_permissions=True)
                    created_items += 1

            # Item Price
            if not self.skip_item_price:
                price_list = _get_value(normalized, "price_list") or self.default_price_list
                price_list_rate = _get_value(
                    normalized,
                    "price_list_rate",
                    "standard_selling_rate",
                    "standard_rate",
                )
                currency = _get_value(normalized, "currency") or default_currency

                if price_list and price_list_rate:
                    rate = flt(price_list_rate)
                    if not self.dry_run:
                        created, updated = _upsert_item_price(
                            item_code=item_code,
                            price_list=price_list,
                            currency=currency,
                            rate=rate,
                            update_existing=bool(self.update_existing),
                        )
                        created_prices += created
                        updated_prices += updated

            # Stock Reconciliation
            if not self.skip_stock_reconciliation:
                warehouse = _get_value(normalized, "warehouse") or self.default_warehouse
                opening_qty = _get_value(normalized, "opening_qty", "opening_stock", "qty")
                valuation_rate = _get_value(
                    normalized, "valuation_rate", "valuation", "rate"
                )

                if warehouse and opening_qty:
                    qty = flt(opening_qty)
                    if qty > 0:
                        key = (item_code, warehouse)
                        if key not in stock_seen:
                            stock_map[warehouse][item_code] = {
                                "qty": qty,
                                "valuation_rate": flt(valuation_rate) if valuation_rate else 0,
                            }
                            stock_seen.add(key)
                        else:
                            log_lines.append(
                                f"Row {row_idx}: Duplicate stock row for {item_code} in {warehouse}; using first occurrence."
                            )

        if errors:
            self._write_log(errors, log_lines, is_error=True)
            frappe.throw("\n".join(errors))

        if not self.skip_stock_reconciliation and not self.dry_run:
            for warehouse, items in stock_map.items():
                if not items:
                    continue
                stock_reco = frappe.new_doc("Stock Reconciliation")
                stock_reco.purpose = "Opening Stock"
                stock_reco.company = default_company
                stock_reco.posting_date = nowdate()
                stock_reco.posting_time = nowtime()
                if difference_account:
                    stock_reco.difference_account = difference_account

                for item_code, payload in items.items():
                    stock_reco.append(
                        "items",
                        {
                            "item_code": item_code,
                            "warehouse": warehouse,
                            "qty": payload["qty"],
                            "valuation_rate": payload.get("valuation_rate", 0),
                        },
                    )

                try:
                    stock_reco.insert(ignore_permissions=True)
                    stock_reco.submit()
                    stock_recos += 1
                except frappe.ValidationError as e:
                    error_msg = str(e)
                    if "Difference Account" in error_msg or "Asset" in error_msg:
                        frappe.throw(
                            f"Invalid Difference Account: {difference_account}. "
                            f"It must be an Asset or Liability type account. "
                            f"Error: {error_msg}"
                        )
                    else:
                        raise

        self._write_log(
            errors,
            log_lines,
            summary={
                "created_items": created_items,
                "updated_items": updated_items,
                "created_prices": created_prices,
                "updated_prices": updated_prices,
                "stock_reconciliations": stock_recos,
            },
        )

        return {
            "created_items": created_items,
            "updated_items": updated_items,
            "created_prices": created_prices,
            "updated_prices": updated_prices,
            "stock_reconciliations": stock_recos,
        }

    def _write_log(self, errors, log_lines, summary=None, is_error=False):
        output = []
        if summary:
            output.append("Summary")
            for key, value in summary.items():
                output.append(f"- {key.replace('_', ' ').title()}: {value}")
            output.append("")

        if log_lines:
            output.append("Notes")
            output.extend(log_lines)
            output.append("")

        if errors:
            output.append("Errors")
            output.extend(errors)

        message = "\n".join(output).strip()
        if message:
            self.db_set("import_log", message)

        if is_error:
            frappe.log_error(message, "Bulk Item Import")


def _normalize_header(header: str) -> str:
    return (header or "").strip().lower().replace(" ", "_").replace("-", "_")


def _get_value(row: dict, *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return ""


def _apply_item_fields(item, normalized, item_name, item_group, stock_uom):
    field_map = {
        "item_name": item_name,
        "item_group": item_group,
        "stock_uom": stock_uom,
        "description": _get_value(normalized, "description"),
        "gst_hsn_code": _get_value(normalized, "gst_hsn_code", "hsn", "hsn_code"),
        "barcode": _get_value(normalized, "barcode"),
        "brand": _get_value(normalized, "brand"),
        "manufacturer": _get_value(normalized, "manufacturer"),
        "disabled": _parse_bool(_get_value(normalized, "disabled")),
        "is_stock_item": _parse_bool(
            _get_value(normalized, "is_stock_item", "is_stock")
        ),
    }

    item_tax = _get_value(normalized, "item_tax", "item_tax_template")

    for field, value in field_map.items():
        if value not in (None, ""):
            item.set(field, value)

    if item_tax:
        item.set("taxes", [])
        item.append("taxes", {"item_tax_template": item_tax})


def _parse_bool(value):
    if value in (None, ""):
        return None
    if isinstance(value, str):
        return cint(value.strip())
    return cint(value)


def _upsert_item_price(item_code, price_list, currency, rate, update_existing=False):
    filters = {
        "item_code": item_code,
        "price_list": price_list,
        "currency": currency,
        "selling": 1,
    }
    existing = frappe.db.get_value("Item Price", filters, "name")

    if existing:
        if update_existing:
            price_doc = frappe.get_doc("Item Price", existing)
            price_doc.price_list_rate = rate
            price_doc.save(ignore_permissions=True)
            return 0, 1
        return 0, 0

    price_doc = frappe.new_doc("Item Price")
    price_doc.item_code = item_code
    price_doc.price_list = price_list
    price_doc.price_list_rate = rate
    price_doc.currency = currency
    price_doc.selling = 1
    price_doc.insert(ignore_permissions=True)
    return 1, 0
