CREATE MIGRATION m15kmxztvvcrfl4mrj3javegpmassqkubuludircdfgjsllpq5cmka
    ONTO m1jc7ioxgejekwdgtadb4kefn3ry4rdh6sd3epkhgps2iyijwez5aq
{
  CREATE MODULE reports IF NOT EXISTS;
  CREATE ALIAS reports::CustomerBalance := (
      billing::Customer {
          outstanding := billing::customer_outstanding(.name)
      }
  );
  CREATE ALIAS reports::InvoicePlan := (
      billing::Invoice {
          multi plan := util::installments(.total_due, .installment_count)
      }
  );
  CREATE ALIAS reports::UnpaidInvoice := (
      SELECT
          billing::Invoice
      FILTER
          NOT (.paid)
  );
};
