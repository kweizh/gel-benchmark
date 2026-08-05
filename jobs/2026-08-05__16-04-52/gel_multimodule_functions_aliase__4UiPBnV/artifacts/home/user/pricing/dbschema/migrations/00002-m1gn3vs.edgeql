CREATE MIGRATION m1gn3vsngxiycbgpwwqenth4rlx2woqld4527u3uzzuefs5txre55q
    ONTO m1xnexea4sgtz7z263iwqxnspr5vqraja24gltm3aucyidvfrg26ca
{
  ALTER FUNCTION util::installments(total: std::decimal, count: std::int64) USING ((IF (count < 1) THEN <std::decimal>{} ELSE (IF (count = 1) THEN {total} ELSE (WITH
      part := 
          util::money_round((total / count))
      ,
      parts := 
          std::array_fill(part, (count - 1))
      ,
      last := 
          (total - std::sum(std::array_unpack(parts)))
  SELECT
      (std::array_unpack(parts) UNION {last})
  ))));
};
