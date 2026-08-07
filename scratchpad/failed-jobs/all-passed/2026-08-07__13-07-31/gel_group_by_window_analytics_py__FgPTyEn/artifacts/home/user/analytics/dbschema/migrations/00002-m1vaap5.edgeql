CREATE MIGRATION m1vaap57su2vnfzch53vp2seumln73tg3vqraibzluu2cv6yipwpmq
    ONTO m1bwpx5zxi42ukjkrmkw4vgcbzlhkieofpu727p33z6sqdthk3u42a
{
  CREATE TYPE default::Refund {
      CREATE REQUIRED LINK sale: default::Sale;
      CREATE REQUIRED PROPERTY amount_cents: std::int64 {
          CREATE CONSTRAINT std::min_value(1);
      };
      CREATE REQUIRED PROPERTY external_id: std::str {
          CREATE CONSTRAINT std::exclusive;
      };
      CREATE REQUIRED PROPERTY refunded_at: std::datetime;
  };
  ALTER TYPE default::Sale {
      CREATE PROPERTY net_cents := ((.amount_cents - (std::sum(.<sale[IS default::Refund].amount_cents) ?? 0)));
      CREATE PROPERTY refund_count := (std::count(.<sale[IS default::Refund]));
      CREATE INDEX ON ((.channel, .occurred_at));
  };
};
