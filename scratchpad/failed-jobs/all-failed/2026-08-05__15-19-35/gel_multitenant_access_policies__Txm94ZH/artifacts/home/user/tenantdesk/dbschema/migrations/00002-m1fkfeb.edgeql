CREATE MIGRATION m1fkfebl5f366nqclve6zqo2i6erxfd3v2f6azupbi5s7ad63nixfq
    ONTO m1aerptxrjxextm3wxi4b7x2d2meeiae3ievgfolcvgcjd57twgsjq
{
  CREATE GLOBAL default::current_actor := (std::assert_single((SELECT
      default::Actor
  FILTER
      (.email = GLOBAL default::current_actor_email)
  )));
  ALTER TYPE default::Ticket {
      ALTER LINK tenant {
          SET readonly := true;
      };
      CREATE ACCESS POLICY delete_own_tenant
          ALLOW DELETE USING ((((GLOBAL default::current_actor).role ?= default::ActorRole.admin) AND (.tenant ?= (GLOBAL default::current_actor).tenant)));
      CREATE ACCESS POLICY insert_own_tenant
          ALLOW INSERT USING (((((GLOBAL default::current_actor).role IN {default::ActorRole.admin, default::ActorRole.agent}) ?? false) AND (.tenant ?= (GLOBAL default::current_actor).tenant)));
      CREATE ACCESS POLICY select_own_tenant
          ALLOW SELECT USING ((.tenant ?= (GLOBAL default::current_actor).tenant));
      CREATE ACCESS POLICY update_own_tenant
          ALLOW UPDATE USING (((((GLOBAL default::current_actor).role IN {default::ActorRole.admin, default::ActorRole.agent}) ?? false) AND (.tenant ?= (GLOBAL default::current_actor).tenant)));
      CREATE CONSTRAINT std::exclusive ON ((.ref, .tenant));
  };
};
