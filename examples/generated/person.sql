select
    cast(p.patient_id as integer) as person_id,
    mg.gender_concept_id as gender_concept_id,
    cast(p.year_of_birth as integer) as year_of_birth,
    cast(null as integer) as month_of_birth,
    cast(null as integer) as day_of_birth,
    cast(null as timestamp_ntz) as birth_datetime,
    mr.race_concept_id as race_concept_id,
    cast(null as integer) as ethnicity_concept_id,
    cast(null as integer) as location_id,
    cast(null as integer) as provider_id,
    cast(null as integer) as care_site_id,
    cast(null as varchar(50)) as person_source_value,
    cast(null as varchar(50)) as gender_source_value,
    cast(null as integer) as gender_source_concept_id,
    cast(null as varchar(50)) as race_source_value,
    cast(null as integer) as race_source_concept_id,
    cast(null as varchar(50)) as ethnicity_source_value,
    cast(null as integer) as ethnicity_source_concept_id
from {{ ref('cai_01_patient') }} as p
left join {{ ref('mapping_person_gender_concept_id') }} as mg
    on p.sex = mg.sex
left join {{ ref('mapping_person_race_concept_id') }} as mr
    on p.indigenous_status = mr.indigenous_status
   and p.country_of_birth = mr.country_of_birth
