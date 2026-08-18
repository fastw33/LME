# Market Product Prices Module

Modulo aislado para recibir capturas OCR de productos de mercado, llamar al worker OCR y guardar resultados en la base `market_product_prices`.

No modifica ni depende del servidor LME existente. Corre como sidecar para mantener separado el scraper LME actual.

## Ejecutar

```powershell
cd LME\market_product_prices
py -3.11 -m venv .venv311
.\.venv311\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python run_market_products.py
```

Por defecto escucha en:

```text
http://127.0.0.1:8030
```

## Endpoints

- `GET /api/market/health`
- `POST /api/market/ocr/uploads`
- `GET /api/market/ocr/batches`
- `GET /api/market/ocr/batches/{batch_id}`
- `POST /api/market/ocr/rows/{row_id}/review`
- `GET /api/market/products`
- `GET /api/market/prices/latest`

## Migraciones

Si la base fue creada con `image_sha256` unico en `ocr_documents`, aplica:

```sql
source sql/2026_07_31_allow_reuploaded_evidence.sql;
```

El modulo permite reenviar la misma captura en otro batch. La tabla `price_history` sigue controlando duplicados por producto, fecha y fuente.
