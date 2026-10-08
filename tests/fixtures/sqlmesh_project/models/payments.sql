MODEL (
  name dataveil_test.payments,
  kind FULL,
  grain id,
);

-- Synthetic, well-known test values: 4111... and 5555... pass the Luhn check,
-- GB29NWBK... is the standard example IBAN. 1234... fails Luhn on purpose.
SELECT * FROM (
  VALUES
    (1, '4111111111111111', 'GB29NWBK60161331926819'),
    (2, '5555555555554444', 'GB29NWBK60161331926819'),
    (3, '1234567812345678', CAST(NULL AS VARCHAR))
) AS t(id, card_number, iban)
