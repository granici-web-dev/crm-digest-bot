from collections.abc import Callable

import pandas as pd

from digest.reports.context import ModuleResult, ReportContext
from digest.reports.modules.seller_format import seller_format_report

ReportModuleFunction = Callable[[pd.DataFrame, ReportContext], ModuleResult]

IMPLEMENTED_MODULES: dict[str, ReportModuleFunction] = {"d1": seller_format_report}
