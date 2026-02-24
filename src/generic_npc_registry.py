from __future__ import annotations
import json
import os
import hashlib
from dataclasses import dataclass, asdict
from datetime import datetime


@dataclass
class GenericNPCIdentity:
    ref_id: str
    assigned_name: str
    bio: str
    voice_model: str
    gender: int
    race: str
    original_game_name: str
    created_at: str


# Names the game gives to generic NPCs — if an NPC's name matches one of these,
# they get assigned a unique persistent identity.
GENERIC_NPC_NAMES: set[str] = {
    "Settler", "Resident", "Scavenger", "Provisioner",
    "Caravan Guard", "Trader", "Merchant", "Farmer",
    "Guard", "Minuteman", "Militia", "Refugee",
    "Wastelander", "Drifter", "Traveler",
}

FEMALE_NAME_POOL: list[str] = [
    "Ada Morgan", "Bess Harlow", "Birdie Sloan", "Bonnie Watts", "Cassie Hale",
    "Clara Finch", "Colleen Pryce", "Daisy Kobb", "Della Voss", "Dixie Marsh",
    "Dot Ramsey", "Edna Creel", "Ellie Vance", "Faye Hollis", "Flora Keene",
    "Gail Munro", "Greta Dorn", "Hazel Briggs", "Ida Croft", "Irene Locke",
    "Jane Dalton", "Josie Trent", "June Wilder", "Kay Ashford", "Lana Phelps",
    "Lena Shore", "Lily Greer", "Loretta Bane", "Louise Hatch", "Lucille Kerr",
    "Mae Hartley", "Maggie Roe", "Maisie Platt", "Martha Vega", "Millie Cross",
    "Mona Steele", "Nora Fisk", "Opal Quinn", "Patience York", "Pearl Gideon",
    "Penny Blake", "Rita Thorne", "Rosie Nye", "Ruby Payne", "Ruth Cavanaugh",
    "Sally Drake", "Sadie Holt", "Stella Crane", "Trudy Lane", "Vera Shelton",
    "Violet Boone", "Wanda Cobb", "Willa Grant", "Winnie Fox", "Zelda Peck",
    "Agnes Whit", "Alma Rudd", "Bea Stark", "Blanche Parr", "Callie Ridge",
    "Cora Jett", "Darlene Moss", "Dottie Nash", "Effie Rowe", "Estelle Barr",
    "Etta Hays", "Fern Oakes", "Gladys Poole", "Harriet Dunn", "Helen Frost",
    "Hilda Brock", "Imogene Slade", "Irma Wolfe", "Jewel Ames", "Kitty Black",
    "Laverne Yates", "Leona Barton", "Lorraine Pugh", "Luella Mead", "Mabel Conn",
    "Marcella Dowd", "Nadine Gibbs", "Nell Rand", "Noreen Flagg", "Olga Stiles",
    "Patsy Blevins", "Peggy Hull", "Prudence Farr", "Reba Cooke", "Regina Sykes",
    "Rhoda Fleet", "Roberta Culp", "Rosalyn Huff", "Rowena Bragg", "Selma Todd",
    "Shirley Kline", "Sybil Hess", "Thelma North", "Tilda Grove", "Verna Polk",
    "Abigail Wren", "Adele Spry", "Althea Hoke", "Annette Judd", "April Cain",
    "Arlene Duvall", "Audrey Gable", "Barbara Fenn", "Beatrix Shaw", "Bernice Lowell",
    "Bessie Cragg", "Betty Roark", "Beverly Smoot", "Billie Nye", "Brenda Shale",
    "Camille Rusk", "Candace Holm", "Carlene Riggs", "Carmen Flint", "Carol Trask",
    "Cathleen Bray", "Cecilia Blunt", "Charlotte Vane", "Claudia Stern", "Constance Hewitt",
    "Corinne Beal", "Darla Graves", "Dawn Kimball", "Deirdre Lockett", "Delilah Thorn",
    "Dolores Quick", "Dorothy Regan", "Earline Spence", "Edith Wynn", "Eleanor Hamm",
    "Eloise Kraft", "Enid Roth", "Ernestine Clay", "Eunice Whitt", "Evelyn Stokes",
    "Fay Trotter", "Felicia Bright", "Francine Tubbs", "Geneva Rush", "Georgia Lund",
    "Gertrude Pace", "Grace Colvin", "Hannah Boles", "Henrietta Crisp", "Hope Rainey",
    "Inez Blackwell", "Ingrid Goff", "Irene Talbot", "Isabel Mace", "Ivy Penn",
    "Jackie Ledford", "Janice Welch", "Jean Pruitt", "Jeanette Darby", "Jolene Sparks",
    "Josephine Sapp", "Joyce Gatlin", "Judith Coyle", "Karen Tripp", "Kathleen Haas",
    "Leila Vick", "Lenora Couch", "Lillian Crosby", "Lorene Saxon", "Lottie Brand",
    "Lucinda Monk", "Lydia Meeks", "Marge Tuggle", "Margot Crowe", "Marion Peak",
    "Marlene Duff", "Maxine Flagler", "Miriam Ledge", "Molly Sutton", "Muriel Teague",
    "Myrtle Hendrix", "Naomi Cates", "Nelda Wheatley", "Norma Pettit", "Olive Coe",
    "Pansy Rigby", "Pauline Bolt", "Ramona Eddy", "Susannah Nall", "Tamara Shook",
]

MALE_NAME_POOL: list[str] = [
    "Abel Marsh", "Amos Whitley", "Barney Croft", "Beau Pryce", "Buck Thorne",
    "Cal Denton", "Cecil Platt", "Chester Doyle", "Clyde Faber", "Dale Hooper",
    "Deacon Nye", "Earl Briggs", "Edgar Keene", "Eli Greer", "Emmett Rowe",
    "Felix Drake", "Floyd Cavanaugh", "Frank Ashby", "Gus Ramsey", "Hank Wilder",
    "Harold Locke", "Harvey Trent", "Homer Finch", "Ira Shelton", "Jasper Hollis",
    "Jed Munro", "Julius Payne", "Karl Hatch", "Lenny Fisk", "Leon Shore",
    "Lester Kerr", "Luther Voss", "Mack Dalton", "Merle Steele", "Milton Bane",
    "Ned Gideon", "Noel Phelps", "Norris Hale", "Oliver Watts", "Orville Quinn",
    "Otis Boone", "Pat Sloan", "Percy Vance", "Pete Harlow", "Phil Crane",
    "Preston Cobb", "Quentin Blake", "Ralph Dorn", "Ray York", "Rex Farley",
    "Roscoe Kobb", "Roy Vega", "Rufus Lane", "Rusty Fox", "Sam Holt",
    "Seth Cross", "Silas Grant", "Slim Peck", "Sterling Flagg", "Stuart Ridge",
    "Teddy Stark", "Tobias Jett", "Vernon Rudd", "Virgil Dunn", "Wade Oakes",
    "Wallace Barr", "Walt Poole", "Ward Frost", "Warren Mead", "Wayne Brock",
    "Webb Slade", "Wesley Ames", "Wilbur Wolfe", "Willis Nash", "Woodrow Hays",
    "Angus Roehl", "Archie Culp", "Bart Gibbs", "Bennett Yates", "Bertram Stiles",
    "Boyd Hull", "Bruno Conn", "Carlton Dowd", "Claude Barton", "Conrad Moss",
    "Daryl Pugh", "Dennis Bragg", "Dwight Cooke", "Earnest Todd", "Elmer Flagg",
    "Everett Sykes", "Fletcher Kline", "Galen Hess", "Gene North", "Gilbert Polk",
    "Glenn Grove", "Gordon Farr", "Grant Huff", "Grover Fleet", "Horace Blevins",
    "Abner Crouch", "Alton Spry", "Arden Judd", "Arnold Cain", "Austin Fenn",
    "Baxter Gable", "Bernard Lowell", "Blaine Cragg", "Boris Roark", "Bradford Smoot",
    "Brant Shale", "Brock Rusk", "Bruce Riggs", "Byron Flint", "Calvin Trask",
    "Carey Bray", "Cassius Blunt", "Charles Vane", "Clement Hewitt", "Clifford Beal",
    "Clinton Graves", "Colby Kimball", "Cornelius Lock", "Curtis Quick", "Dallas Regan",
    "Damon Spence", "Darren Wynn", "Davis Hamm", "Dean Kraft", "Delbert Roth",
    "Dominic Clay", "Donovan Whitt", "Doyle Stokes", "Duncan Bright", "Duane Tubbs",
    "Edmund Rush", "Eldon Lund", "Ellis Pace", "Elroy Colvin", "Ernest Boles",
    "Ervin Crisp", "Eugene Rainey", "Farley Goff", "Fergus Talbot", "Forrest Mace",
    "Garland Penn", "Garrett Welch", "Gerald Pruitt", "Gideon Darby", "Graham Sparks",
    "Hal Sapp", "Hamilton Gatlin", "Harlan Coyle", "Harrison Tripp", "Heath Haas",
    "Herschel Vick", "Howard Couch", "Hudson Crosby", "Hugh Saxon", "Irving Brand",
    "Isaac Monk", "Ivan Meeks", "Jarvis Crowe", "Jefferson Peak", "Jerome Duff",
    "Jonas Sutton", "Kendrick Teague", "Landon Hendrix", "Leland Cates", "Lewis Wheatley",
    "Lloyd Pettit", "Lyle Coe", "Malcolm Flagler", "Marshall Ledge", "Marvin Tuggle",
    "Maxwell Rainey", "Morgan Pratt", "Murray Trimble", "Myron Phipps", "Nathan Whitacre",
    "Niles Akin", "Norman Crabtree", "Otho Siler", "Owen Padgett", "Palmer Rigby",
    "Randolph Bolt", "Roland Eddy", "Ruben Nall", "Sanford Shook", "Sheldon Pratt",
]

PERSONALITY_TRAITS: list[str] = [
    "cautious", "hot-headed", "quiet and observant", "friendly but guarded",
    "world-weary", "optimistic despite everything", "deeply suspicious of strangers",
    "fiercely loyal", "pragmatic to a fault", "haunted by past losses",
    "surprisingly cheerful", "gruff but kind-hearted", "nervous and jumpy",
    "stoic and dependable", "cynical but fair", "always looking for an angle",
    "determined and stubborn", "gentle-natured", "wary but curious",
    "hardened by the wasteland",
]

OCCUPATIONS: list[str] = [
    "tending crops", "patrolling the perimeter", "fixing broken equipment",
    "scavenging for supplies", "trading with passing caravans",
    "cooking meals for the settlement", "maintaining the water purifier",
    "building defenses", "caring for the brahmin", "keeping watch at night",
    "repairing weapons and armor", "teaching younger settlers",
    "brewing moonshine on the side", "collecting salvage from nearby ruins",
    "tinkering with old pre-war tech", "hunting radstag and mirelurk",
    "running messages between settlements", "tanning leather and hides",
    "growing mutfruit and tatos", "standing guard at the gate",
]

BACKSTORY_FRAGMENTS: list[str] = [
    "came from a vault that opened years ago",
    "lost their family to raiders and started over",
    "used to run with a trading caravan before settling down",
    "survived alone in the ruins for years before finding this place",
    "was rescued by Minutemen and decided to stay",
    "doesn't talk much about where they came from",
    "grew up in the wasteland and never knew anything else",
    "fled from a settlement that was overrun by super mutants",
    "used to be a farmer before the crops failed",
    "wandered the Commonwealth for months before finding safety here",
    "had a run-in with the Institute that they don't like to discuss",
    "came from out west looking for a fresh start",
    "was once part of a larger group that scattered after an attack",
    "traded their way across the Commonwealth to get here",
    "learned everything they know from an old wastelander who's gone now",
    "barely survived a radstorm and was nursed back to health here",
    "left Diamond City because they couldn't afford to stay",
    "has been here longer than most and remembers when it was just empty ground",
    "arrived half-starved and never left",
    "keeps to themselves mostly but always helps when asked",
]


def _hash_pick(ref_id: str, salt: str, pool: list) -> int:
    """Deterministic index selection via SHA-256."""
    digest = hashlib.sha256((ref_id + salt).encode()).hexdigest()
    return int(digest, 16) % len(pool)


class GenericNPCRegistry:
    def __init__(self, registry_path: str):
        self._path = registry_path
        self._entries: dict[str, GenericNPCIdentity] = {}
        self._load()

    def _load(self):
        if os.path.exists(self._path):
            with open(self._path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            for ref_id, data in raw.items():
                self._entries[ref_id] = GenericNPCIdentity(**data)

    def lookup(self, ref_id: str) -> GenericNPCIdentity | None:
        return self._entries.get(ref_id)

    def register(self, ref_id: str, gender: int, race: str, original_name: str,
                 voice_pool: dict[str, list[str]]) -> GenericNPCIdentity:
        name = self._assign_name(ref_id, gender)
        voice = self._assign_voice(ref_id, gender, voice_pool)
        bio = self._generate_bio(ref_id, name, gender, race)

        identity = GenericNPCIdentity(
            ref_id=ref_id,
            assigned_name=name,
            bio=bio,
            voice_model=voice,
            gender=gender,
            race=race,
            original_game_name=original_name,
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
        self._entries[ref_id] = identity
        self.save()
        return identity

    def save(self):
        tmp = self._path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({k: asdict(v) for k, v in self._entries.items()}, f, indent=2)
        os.replace(tmp, self._path)

    def _assign_name(self, ref_id: str, gender: int) -> str:
        pool = FEMALE_NAME_POOL if gender == 1 else MALE_NAME_POOL
        idx = _hash_pick(ref_id, "name", pool)
        return pool[idx]

    def _assign_voice(self, ref_id: str, gender: int,
                      voice_pool: dict[str, list[str]]) -> str:
        key = "female" if gender == 1 else "male"
        pool = voice_pool.get(key, [])
        if not pool:
            return ""
        idx = _hash_pick(ref_id, "voice", pool)
        return pool[idx]

    def _generate_bio(self, ref_id: str, name: str, gender: int, race: str) -> str:
        trait = PERSONALITY_TRAITS[_hash_pick(ref_id, "trait", PERSONALITY_TRAITS)]
        occupation = OCCUPATIONS[_hash_pick(ref_id, "occupation", OCCUPATIONS)]
        backstory = BACKSTORY_FRAGMENTS[_hash_pick(ref_id, "backstory", BACKSTORY_FRAGMENTS)]
        pronoun = "She" if gender == 1 else "He"
        return (
            f"{name} is a {trait} {race.lower()} wastelander. "
            f"{pronoun} spends most days {occupation}. "
            f"{name} {backstory}."
        )
