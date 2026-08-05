CREATE MIGRATION m1aerptxrjxextm3wxi4b7x2d2meeiae3ievgfolcvgcjd57twgsjq
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
      CREATE REQUIRED PROPERTY ref: std::str;
      CREATE INDEX ON (.ref);
      CREATE REQUIRED PROPERTY status: default::TicketStatus {
          SET default := (default::TicketStatus.open);
      };
      CREATE REQUIRED PROPERTY subject: std::str;
  };
};
