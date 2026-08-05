CREATE MIGRATION m1f5wase4262l5js6tnzz3r5z77g6m3gsheionmggiupza3l43cszq
    ONTO initial
{
  CREATE SCALAR TYPE default::ActorRole EXTENDING enum<admin, agent, readonly>;
  CREATE SCALAR TYPE default::TicketStatus EXTENDING enum<open, pending, closed>;
  CREATE GLOBAL default::current_actor_email -> std::str;
  CREATE TYPE default::Tenant {
      CREATE REQUIRED PROPERTY name: std::str;
      CREATE REQUIRED PROPERTY slug: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
  };
  CREATE TYPE default::Actor {
      CREATE REQUIRED PROPERTY email: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED LINK tenant: default::Tenant;
      CREATE REQUIRED PROPERTY role: default::ActorRole;
  };
  CREATE GLOBAL default::current_actor := (SELECT
      default::Actor
  FILTER
      (.email = GLOBAL default::current_actor_email)
  );
  CREATE TYPE default::Ticket {
      CREATE REQUIRED LINK tenant: default::Tenant;
      CREATE ACCESS POLICY delete_tickets
          ALLOW DELETE USING ((((GLOBAL default::current_actor).role ?= <default::ActorRole>'admin') AND (.tenant ?= (GLOBAL default::current_actor).tenant)));
      CREATE ACCESS POLICY insert_tickets
          ALLOW INSERT USING (((((GLOBAL default::current_actor).role ?= <default::ActorRole>'admin') OR ((GLOBAL default::current_actor).role ?= <default::ActorRole>'agent')) AND (.tenant ?= (GLOBAL default::current_actor).tenant)));
      CREATE ACCESS POLICY select_tickets
          ALLOW SELECT USING ((.tenant ?= (GLOBAL default::current_actor).tenant));
      CREATE ACCESS POLICY update_tickets
          ALLOW UPDATE USING (((((GLOBAL default::current_actor).role ?= <default::ActorRole>'admin') OR ((GLOBAL default::current_actor).role ?= <default::ActorRole>'agent')) AND (.tenant ?= (GLOBAL default::current_actor).tenant)));
      CREATE REQUIRED PROPERTY ref: std::str;
      CREATE CONSTRAINT std::exclusive ON ((.ref, .tenant));
      CREATE INDEX ON (.ref);
      CREATE REQUIRED PROPERTY status: default::TicketStatus {
          SET default := (default::TicketStatus.open);
      };
      CREATE REQUIRED PROPERTY subject: std::str;
  };
};
