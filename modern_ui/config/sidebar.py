def get_config():
    return {
        "Bulk Imports": {
            "icon": "fa fa-upload",
            "color": "#4CAF50",
            "items": [
                {
                    "type": "doctype",
                    "name": "Bulk Item Import",
                    "description": "Import Items, Stock, and Pricing in a single CSV file",
                    "icon": "fa fa-file-csv",
                    "label": "Bulk Item Import",
                },
                {
                    "type": "link",
                    "name": "Item",
                    "icon": "fa fa-shopping-bag",
                    "label": "Items",
                },
            ],
        }
    }
