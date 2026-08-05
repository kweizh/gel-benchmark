CREATE MIGRATION m173a4zh3676fberdhtviie67mtwp2kygqlqh4mdjvl3rw26soll3q
    ONTO m1seljvurohu5n3w4egnafp2ogmwkdyfvp5du6v3vgmtvqxvsvznza
{
  ALTER TYPE default::Ticket {
      ALTER ACCESS POLICY tenant_delete USING ((.tenant = (std::assert_exists((SELECT
          default::Actor
      FILTER
          ((.email = GLOBAL default::current_actor_email) AND (.role = default::ActorRole.admin))
      ))).tenant));
      ALTER ACCESS POLICY tenant_insert USING ((.tenant = (std::assert_exists((SELECT
          default::Actor
      FILTER
          ((.email = GLOBAL default::current_actor_email) AND (.role IN {default::ActorRole.admin, default::ActorRole.agent}))
      ))).tenant));
      ALTER ACCESS POLICY tenant_select USING ((.tenant = (std::assert_exists((SELECT
          default::Actor
      FILTER
          (.email = GLOBAL default::current_actor_email)
      ))).tenant));
      ALTER ACCESS POLICY tenant_update_read USING ((.tenant = (std::assert_exists((SELECT
          default::Actor
      FILTER
          ((.email = GLOBAL default::current_actor_email) AND (.role IN {default::ActorRole.admin, default::ActorRole.agent}))
      ))).tenant));
      ALTER ACCESS POLICY tenant_update_write USING ((.tenant = (std::assert_exists((SELECT
          default::Actor
      FILTER
          ((.email = GLOBAL default::current_actor_email) AND (.role IN {default::ActorRole.admin, default::ActorRole.agent}))
      ))).tenant));
  };
};
