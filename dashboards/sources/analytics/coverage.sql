select
    company, scope, statement, figures, mapped, cast(mapped_share as double) as mapped_share,
    claude_unverified
from coverage
