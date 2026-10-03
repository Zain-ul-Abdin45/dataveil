MODEL (
  name dataveil_test.people,
  kind FULL,
  grain id,
);

SELECT * FROM (
  VALUES
    (1, 'alice.wonderland@example.com', '123-45-6789', '  Alice  '),
    (2, 'bob@example.com', '987-65-4321', 'Bob'),
    (3, CAST(NULL AS VARCHAR), CAST(NULL AS VARCHAR), CAST(NULL AS VARCHAR))
) AS t(id, email, ssn, full_name)
