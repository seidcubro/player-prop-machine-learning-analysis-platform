ALTER TABLE injuries DROP CONSTRAINT IF EXISTS injuries_pkey;
ALTER TABLE depth_charts DROP CONSTRAINT IF EXISTS depth_charts_pkey;
SELECT 'constraints dropped' AS status;
