import datetime as dt
from typing import List, Optional

import attr
import cattr
import requests
import pytz


@attr.s(auto_attribs=True)
class Variant:
    is_control: bool
    slug: bool
    ratio: int


# only temporary, can be removed once normandy slug is part of experiment
# see https://github.com/mozilla/experimenter/issues/2318
@attr.s(auto_attribs=True)
class Recipe:
    slug: Optional[str]  # normandy slug


@attr.s(auto_attribs=True)
class Experiment:
    slug: str  # experimenter slug
    normandy_slug: str
    start_date: Optional[dt.datetime]
    end_date: Optional[dt.datetime]
    variants: List[Variant]


@attr.s(auto_attribs=True)
class ExperimentCollection:
    experiments: List[Experiment] = attr.Factory(list)

    EXPERIMENTER_API_EXPERIMENTS_URL = (
        "https://experimenter.services.mozilla.com/api/v1/experiments/"
    )

    @staticmethod
    def _unix_millis_to_datetime(num: Optional[float]) -> dt.datetime:
        if num is None:
            return None
        return dt.datetime.fromtimestamp(num / 1e3, pytz.utc)

    @classmethod
    def from_experimenter(cls, session: requests.Session = None) -> "ExperimentCollection":
        session = session or requests.Session()
        experiments = session.get(cls.EXPERIMENTER_API_EXPERIMENTS_URL).json()
        converter = cattr.Converter()
        converter.register_structure_hook(
            dt.datetime, lambda num, _: cls._unix_millis_to_datetime(num),
        )
        return cls([converter.structure(experiment, Experiment) for experiment in experiments])

    def started_since(self, since: dt.datetime) -> "ExperimentCollection":
        """since should be a tz-aware datetime in UTC."""
        cls = type(self)
        return cls([ex for ex in self.experiments if ex.start_date and ex.start_date >= since])

    def end_after(self, after: dt.datetime) -> "ExperimentCollection":
        """All experiments that end after the specified date."""
        cls = type(self)
        return cls([ex for ex in self.experiments if ex.end_date and ex.end_date >= after])
