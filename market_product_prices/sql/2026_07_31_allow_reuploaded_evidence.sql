USE market_product_prices;

-- Las capturas pueden reenviarse en pruebas o en reprocesos.
-- El historico evita duplicados por producto/fecha/fuente; la evidencia no debe bloquear el batch.
SET @drop_index_sql := (
  SELECT IF(
    COUNT(*) > 0,
    'ALTER TABLE ocr_documents DROP INDEX uq_document_image_sha256',
    'SELECT ''uq_document_image_sha256 no existe'' AS info'
  )
  FROM information_schema.statistics
  WHERE table_schema = DATABASE()
    AND table_name = 'ocr_documents'
    AND index_name = 'uq_document_image_sha256'
);

PREPARE drop_index_stmt FROM @drop_index_sql;
EXECUTE drop_index_stmt;
DEALLOCATE PREPARE drop_index_stmt;
