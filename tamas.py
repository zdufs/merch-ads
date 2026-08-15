#!/usr/bin/env python3
"""Legacy campaign classifier.

TAMAS was a manual campaign strategy this engine used to automate: one broad
keyword and one ASIN per campaign, fixed bids, judged on total royalty minus ad
spend rather than on ACOS. The strategy was retired, and its builder, optimizer
and candidate finder were deleted with it.

What survives is the name test. TAMAS campaigns are name-prefixed, and archived
ones stay in the local mirror, so phase 2 and phase 3 still need to recognise
them in order to leave them out of the standard per-type ACOS rules — and the
campaign browser still needs to label them. Three lines, no behaviour.

The organic-halo estimate this strategy originally motivated outlived it and now
covers every campaign type; see halo.py.
"""

PREFIX = "TAMAS - "


def is_tamas(campaign_name):
    return (campaign_name or "").startswith(PREFIX)
