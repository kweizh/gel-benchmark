CREATE MIGRATION m1gelfxxsl3rfq22yt5am6vyvevjmqsdkkpovtb2jckvwozhyf7ddq
    ONTO m1hjfkba67fxvnsedmcnggp2orj77435nm3b2hizb6b6fqsdiuzi2a
{
  ALTER ABSTRACT CONSTRAINT default::token_invalid USING ((std::re_test('[A-Z][A-Z0-9_]*', <std::str>__subject__) AND (std::len(<std::str>__subject__) <= max)));
};
