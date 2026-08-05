CREATE MIGRATION m1seljvurohu5n3w4egnafp2ogmwkdyfvp5du6v3vgmtvqxvsvznza
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
      CREATE REQUIRED LINK tenant: default::Tenant;
      CREATE REQUIRED PROPERTY email: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY role: default::ActorRole;
  };
  CREATE TYPE default::Ticket {
      CREATE REQUIRED LINK tenant: default::Tenant;
      CREATE ACCESS POLICY tenant_delete
          ALLOW DELETE USING (EXISTS ((SELECT
              default::Actor
          FILTER
              (((.email = GLOBAL default::current_actor_email) AND (.role = default::ActorRole.admin)) AND (.tenant = default::Ticket.tenant))
          )));
      CREATE ACCESS POLICY tenant_insert
          ALLOW INSERT USING (EXISTS ((SELECT
              default::Actor
          FILTER
              (((.email = GLOBAL default::current_actor_email) AND (.role IN {default::ActorRole.admin, default::ActorRole.agent})) AND (.tenant = default::Ticket.tenant))
          )));
      CREATE ACCESS POLICY tenant_select
          ALLOW SELECT USING (EXISTS ((SELECT
              default::Actor
          FILTER
              ((.email = GLOBAL default::current_actor_email) AND (.tenant = default::Ticket.tenant))
          )));
      CREATE ACCESS POLICY tenant_update_read
          ALLOW UPDATE READ USING (EXISTS ((SELECT
              default::Actor
          FILTER
              (((.email = GLOBAL default::current_actor_email) AND (.role IN {default::ActorRole.admin, default::ActorRole.agent})) AND (.tenant = default::Ticket.tenant))
          )));
      CREATE ACCESS POLICY tenant_update_write
          ALLOW UPDATE WRITE USING (EXISTS ((SELECT
              default::Actor
          FILTER
              (((.email = GLOBAL default::current_actor_email) AND (.role IN {default::ActorRole.admin, default::ActorRole.agent})) AND (.tenant = default::Ticket.tenant))
          )));
      CREATE REQUIRED PROPERTY ref: std::str;
      CREATE CONSTRAINT std::exclusive ON ((.ref, .tenant));
      CREATE REQUIRED PROPERTY status: default::TicketStatus {
          SET default := (default::TicketStatus.open);
      };
      CREATE REQUIRED PROPERTY subject: std::str;
  };
};
