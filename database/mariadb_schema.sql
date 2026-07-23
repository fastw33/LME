CREATE DATABASE IF NOT EXISTS metal_harvest_lme
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE metal_harvest_lme;

CREATE TABLE IF NOT EXISTS lme_metals (
  metal_id INT UNSIGNED NOT NULL AUTO_INCREMENT,
  metal_key VARCHAR(80) NOT NULL,
  name_es VARCHAR(120) NOT NULL,
  name_en VARCHAR(120) NOT NULL,
  slug_lme VARCHAR(120) NOT NULL,
  url_lme VARCHAR(600) NOT NULL,
  is_active TINYINT(1) NOT NULL DEFAULT 1,
  display_order INT UNSIGNED NOT NULL DEFAULT 100,
  notes TEXT NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  updated_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6)
    ON UPDATE CURRENT_TIMESTAMP(6),
  PRIMARY KEY (metal_id),
  UNIQUE KEY uq_lme_metals_key (metal_key),
  UNIQUE KEY uq_lme_metals_slug (slug_lme),
  KEY idx_lme_metals_active_order (is_active, display_order)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS lme_scrape_runs (
  run_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  trigger_type ENUM('manual', 'scheduled_07', 'scheduled_14', 'retry', 'test') NOT NULL DEFAULT 'manual',
  status ENUM('running', 'ok', 'partial', 'failed') NOT NULL DEFAULT 'running',
  started_at DATETIME(6) NOT NULL,
  finished_at DATETIME(6) NULL,
  rows_ok SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  rows_failed SMALLINT UNSIGNED NOT NULL DEFAULT 0,
  message TEXT NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (run_id),
  KEY idx_lme_runs_started_at (started_at),
  KEY idx_lme_runs_status (status)
) ENGINE=InnoDB;

CREATE TABLE IF NOT EXISTS lme_scraped_prices (
  price_id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
  run_id BIGINT UNSIGNED NOT NULL,
  metal_id INT UNSIGNED NOT NULL,
  source_name VARCHAR(160) NOT NULL,
  source_url VARCHAR(600) NOT NULL,
  price DECIMAL(18,6) NULL,
  currency CHAR(3) NOT NULL DEFAULT 'USD',
  unit VARCHAR(40) NOT NULL DEFAULT 'tonelada métrica',
  variation_percent DECIMAL(10,6) NULL,
  price_basis VARCHAR(180) NOT NULL,
  data_timestamp DATETIME(6) NULL,
  scraped_at DATETIME(6) NOT NULL,
  status ENUM('ok', 'failed') NOT NULL DEFAULT 'ok',
  error_message TEXT NULL,
  raw_excerpt TEXT NULL,
  created_at DATETIME(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
  PRIMARY KEY (price_id),
  KEY idx_lme_prices_run_id (run_id),
  KEY idx_lme_prices_metal_scraped (metal_id, scraped_at),
  KEY idx_lme_prices_data_timestamp (data_timestamp),
  KEY idx_lme_prices_status (status),
  UNIQUE KEY uq_lme_price_run_metal_basis (run_id, metal_id, price_basis),
  CONSTRAINT fk_lme_prices_run
    FOREIGN KEY (run_id) REFERENCES lme_scrape_runs (run_id),
  CONSTRAINT fk_lme_prices_metal
    FOREIGN KEY (metal_id) REFERENCES lme_metals (metal_id)
) ENGINE=InnoDB;

INSERT INTO lme_metals
  (metal_key, name_es, name_en, slug_lme, url_lme, display_order)
VALUES
  ('lme_lead', 'Plomo LME', 'Lead', 'lead', 'https://www.lme.com/metals/non-ferrous/lme-lead#Summary', 10),
  ('lme_nickel', 'Níquel LME', 'Nickel', 'nickel', 'https://www.lme.com/metals/non-ferrous/lme-nickel#Summary', 20),
  ('lme_copper', 'Cobre LME', 'Copper', 'copper', 'https://www.lme.com/metals/non-ferrous/lme-copper#Overview', 30),
  ('lme_tin', 'Estaño LME', 'Tin', 'tin', 'https://www.lme.com/metals/non-ferrous/lme-tin#Summary', 40),
  ('lme_zinc', 'Zinc LME', 'Zinc', 'zinc', 'https://www.lme.com/metals/non-ferrous/lme-zinc#Summary', 50),
  ('smm_tungsten', 'Tungsteno SMM', 'Tungsten', 'tungsten', 'https://www.metal.com/es/tungsten#Tungsteno', 60)
ON DUPLICATE KEY UPDATE
  name_es = VALUES(name_es),
  name_en = VALUES(name_en),
  url_lme = VALUES(url_lme),
  display_order = VALUES(display_order),
  is_active = 1;
