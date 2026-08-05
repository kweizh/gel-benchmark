CREATE MIGRATION m1xp53obtkzyzaeouhtp2hlmqqtigwi3dmrkkmfggj7xay3x7v2esq
    ONTO m17t3kjgpmu64loaamwprc6uukyuwxc4ezq67j46ld7eeh654iasnq
{
  ALTER TYPE default::Comment {
      ALTER LINK document {
          ON TARGET DELETE DELETE SOURCE;
      };
  };
};
