def get_clean_boss_name(unit_id: str) -> str:
    name = unit_id.lower()

    # Keyword mapping: If keyword is "in" the ID, return the clean name
    name_map = {

        # ===========================================
        "riptide": "Tau Riptide",
        "taumarksman": "Sho'syl",
        "taucrisis":"Re'vas",
        # ===========================================
        "avatar": "Avatar of Khaine",
        "autarch": "Aethana",
        "farseer": "Eldryon",
        # ===========================================
        "silentking": "Szarekh the Silent King",
        "necromenhir":"Menhir",

        # ===========================================
        "ghazghkull": "Ghazghkull Thraka",
        "bigmek": "Gibbascrapz",
        "orksnob": "Tanksmasha",
        # ===========================================
        "mortarion": "Mortarion",
        "blight": "Corrodius",
        "rotbone":"Rotbone",
        # ===========================================
        "magnus": "Magnus the Red",
        "thousinfernalmaster": "Abraxas",
        "thoussorcerer": "Thaumachus",
        # ===========================================
        "tervigon": "Tervigon",
        "hive": "Hive Tyrant",
        "screamer": "Screamer-Killer",
        # ===========================================
        "belisarius": "Belisarius Cawl",
        "marshall": "Tan Gi'da",
        "manipulus":"Actus",
        # ===========================================
        "rogaldorn": "Rogal Dorn Battle Tank",
        "ordnance":"Chaddeus Noble",
        "primarispsy":"Sibyll Devine",
        # ===========================================


    }

    for keyword, clean_name in name_map.items():
        if keyword in name:
            return clean_name

    # Fallback: if no keyword matches, just remove "GuildBoss" and "Boss"
    return unit_id.replace("GuildBoss", "").replace("Boss", " ")



def get_mow_emoji(machine_of_war: str) -> str:
    if not machine_of_war:
        return "❓"
    name = machine_of_war.lower()

    mow_map = {

        "ultradreadnought": "<:Galatian:1471171616984793310>",
        "tyranbiovore":"<:Biovore:1476686953914044559>",
        "blackforgefiend":"<:Forge:1476624443810910421>",
        "adeptexorcist":"<:Exorcist:1471171637431898244>",
        "daemonprince":"<:zkar:1471171718121783347>",
        "crawler":"<:Fart_tank:1476626311777878016>",



    }
    for keyword, emoji in mow_map.items():
        if keyword in name:
            return emoji

    return machine_of_war #"❓"



def get_boss_emoji(unit_id: str) -> str:
    if not unit_id:
        return "❓"

    # Convert "GuildBoss11Boss1TauRiptide" to lowercase
    name = unit_id.lower()

    # 1. Tyranid/Hive Logic (Highest Priority)
    #if "tervigon" in name or "hive" in name:
        #if "leviathan" in name: return "<:Terv:1468464192934772757>"
       # if "gorgon" in name:    return "<:Tyrant:1468464190460137566>"
        #if "kronos" in name:    return "<:Tyrant:1468464190460137566>"

    # 3. Use a Dictionary for the "Simple" matches
    # This checks if the key (e.g., 'riptide') exists anywhere in 'GuildBoss11Boss1TauRiptide'
    boss_map = {

        # ===========================================

        "tyrant":"<:Tyrant:1468464190460137566>",

        "tervigon":"<:Terv:1468464192934772757>",

        "screamer": "<:ScreamerKiller:1468468660946600099>",

        "neurothrope":"<:Neuro:1468464145870618708>",

        "wingedprime":"<:WingedPrime:1468464191777280144>",

        #===========================================

        "abaddon":"<:Abaddon:1471171619656433797>",

        "blackpossession":"<:Archi:1476624607552212992>",

        # ===========================================



        "belisarius": "<:Cawl:1468465678314115276>",

        "marshall": " <:TanGida:1468465670059589785>",

        "manipulus": "<:Actus:1468465674480517233>",

        "admecdominus": "<:Vitruvius:1471171701604876379>",

        "admecruststalker": "<:Rho:1471171692335337624>",

        # ===========================================

        "exultant":"<:Laviscus:1473674392319037562> ",

        # ===========================================

        "magnus": "<:Magnus:1468466564994043914>",

        "thousinfernalmaster": "<:Abraxas:1468466561697452173>",

        "thoustzaangor":"<:Yaz:1476686931449483539>",

        "thoussorcerer":"<:Thaumachus:1468466560825037020>",

        "thousahriman":"<:Ahriman:1476686898092183622>",

        # ===========================================

        "custobladechampion": "<:Kariyan:1473674468734800046>",

        "custotrajann": "<:Trajann:1471171699373379625>",

        "custovexiluspraetor":"<:Aesoth:1476625528755585056>",

        "custoatlacoya":"<:Altacoya:1471171623146225787>",

        # ===========================================

        "genesMagus":"<:Xybia:1476714001550413824>",

        # ===========================================

        "necrospyder":"<:Aleph:1476626659565244628>",

        # ===========================================

        "worldkharn": "<:Kharn:1476628049272246435>",
        "worldexecutions":"<:Tarvakh:1476628040631976209>",

        # ===========================================

        "spaceblackmane": "<:Ragnar:1471171706436587560>",

        # ===========================================

        "blooddante": "<:Dante:1471171632117579782>",

        "bloodmephiston":"<:Mephedrone_guy:1476624993587695807>",

        # ===========================================

        "templhelbrecht":"<:Helbrecht:1471171703496380499>",

        # ===========================================

        "riptide": "<:Riptide:1468464806368378985>",

        "taumarksman": "<:Sho:1468464807282872424>",

        "tauaunshi":"<:Aunshi:1471171627390599281>",

        "taucrisis":"<:Revas:1468464811540222064>",

        "taufarsight":"<:Farsight:1475266591829528851>",

        "taudarkstrider":"<:Dark_strider:1476686907084636231>",

        # ===========================================

        "runtherd": "<:Snot:1476713922294714401>",

        "orkswarboss": "<:BossG:1476713924727668737>",

        "ghazghkull": "<:Ghaz:1468465667249410180>",

        "orksnob": "<:Tanksmasha:1468465671170949171>",

        "bigmek": "<:Gibba:1468465669187309679>",

        # ===========================================

        "avatar": "<:Avatar:1468465676837453986>",

        "farseer":"<:Eldryon:1471171634760257840>",

        "autarch":"<:Aethana:1468465675868573931>",

        # ===========================================

        "silentking": "<:Szarekh:1468466830883688674>",

        "necromenhir": "<:Menhir:1468466831978397716>",

        # ===========================================



        "mortarion": "<:Mortarion:1468466566902452430>",

        "blight":"<:Corrodius:1468466563731689534>",

        "rotbone":"<:Rotbone:1468466559562547314>",

        # ===========================================

        "adeptcanoness":"<:Roswitha:1476623226414174421>",

        "adeptcelestine":"<:Celestine:1476623311416070196>",
        "adeptmorvenn":"<:Morvenn:1476623445147254844>",
        "adeptretributor":"<:Vindicta:1476623561547448352>",

        # ===========================================




        "rogal": "<:Dorn:1468464810348777616>",

        "ordnance": "<:Thad:1468464809048670220>",

        "primarispsy": "<:Sibyll:1468464808251625535>",


        # ===========================================
        # Assorted other characters
        # ===========================================
        "ultracalgar":    "<:Calgar:1476686945051738254>",
        "darkaasmodai":   "<:Asmo:1476625574561714388>",
        "darkacompanion": "<:Forcas:1476625758997970964>",

        "votanmemnyr":    "<:Ammuk:1480629452936445973>",
        "spacewulfen":    "<:Ulf:1476714131640815646>",


        "bloodDeathCompany": "<:Lucien:1476624849899225281>",
        "eldarRanger":       "<:Calandis:1476624101106909427>",
        "templChampion":     "<:Jaeger:1476714016276480163>",

    }

    # Loop through the map and return the first match found
    for keyword, emoji in boss_map.items():
        if keyword in name:
            return emoji

    # 4. Default if no keywords match
    return unit_id #"❓"

# Testing with changing numbers:
# print(get_boss_emoji("GuildBoss99Boss5TauRiptide")) -> ":Riptide:"
# print(get_boss_emoji("GuildBoss1234_Mortarion_Chaos")) -> ":Mortarion:"