from dataclasses import dataclass


@dataclass
class MysteryTopic:
    id: str
    title: str
    premise: str
    setting: str

    crime_type: str
    investigation_type: str
    protagonist: str
    central_mystery: str
    twist_type: str
    ending_type: str