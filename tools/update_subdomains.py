# -*- coding: utf-8 -*-
import json
import re
from pathlib import Path

RAW_DOMAINS = """
mail.com
email.com
usa.com
myself.com
consultant.com
post.com
europe.com
asia.com
iname.com
writeme.com
dr.com
cheerful.com
execs.com
groupmail.com
homemail.com
housemail.com
workmail.com
inorbit.com
mail-me.com
planetmail.com
solution4u.com
tech-center.com
webname.com
cash4u.com
accountant.com
activist.com
adexec.com
allergist.com
alumni.com
alumnidirector.com
archaeologist.com
birdlover.com
chemist.com
columnist.com
comic.com
computer4u.com
counsellor.com
cyberservices.com
deliveryman.com
disposable.com
fastservice.com
financier.com
gardener.com
geologist.com
graphic-designer.com
hot-shot.com
instruction.com
insurer.com
job4u.com
journalist.com
legislator.com
lobbyist.com
minister.com
net-shopping.com
optician.com
pediatrician.com
politician.com
presidency.com
priest.com
publicist.com
qualityservice.com
realtyagent.com
registerednurses.com
repairman.com
representative.com
rescueteam.com
sociologist.com
techie.com
technologist.com
theplate.com
toothfairy.com
tvstar.com
umpire.com
worker.com
aircraftmail.com
blader.com
boardermail.com
brew-meister.com
brew-master.com
bsdmail.com
cutey.com
hackermail.com
hilarious.com
keromail.com
nonpartisan.com
rocketship.com
cyberdude.com
cybergal.com
cyber-wizard.com
appraiser.com
auctioneer.com
fireman.com
photographer.com
physicist.com
programmer.com
radiologist.com
salesperson.com
secretary.com
socialworker.com
songwriter.com
artlover.com
bikerider.com
catlover.com
dbzmail.com
doglover.com
doramail.com
galaxyhit.com
kittymail.com
lovecat.com
marchmail.com
petlover.com
snakebite.com
toke.com
uymail.com
acdcfan.com
discofan.com
elvisfan.com
hiphopfan.com
kissfans.com
madonnafan.com
metalfan.com
ninfan.com
ravemail.com
reborn.com
reggaefan.com
arcticmail.com
2trom.com
bellair.com
californiamail.com
dallasmail.com
nycmail.com
pacific-ocean.com
pacificwest.com
sanfranmail.com
africamail.com
asia-mail.com
australiamail.com
berlin.com
brazilmail.com
chinamail.com
dublin.com
dutchmail.com
englandmail.com
europemail.com
germanymail.com
irelandmail.com
israelmail.com
italymail.com
koreamail.com
mexicomail.com
moscowmail.com
munich.com
polandmail.com
safrica.com
samerica.com
scotlandmail.com
spainmail.com
swedenmail.com
swissmail.com
torontomail.com
appraiser.net
auctioneer.net
bartender.net
chef.net
contractor.net
coolsite.net
fireman.net
hairdresser.net
instructor.net
orthodontist.net
photographer.net
physicist.net
planetmail.net
programmer.net
radiologist.net
salesperson.net
secretary.net
socialworker.net
songwriter.net
surgical.net
therapist.net
greenmail.net
humanoid.net
null.net
bellair.net
angelic.com
atheist.com
disciples.com
innocent.com
muslim.com
protestant.com
reincarnate.com
religious.com
saintly.com
clubmember.org
collector.org
graduate.org
musician.org
teachers.org
linuxmail.org
"""

def main():
    prefixes = []
    for line in RAW_DOMAINS.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        prefix = line.split(".")[0]
        if prefix and prefix not in prefixes:
            prefixes.append(prefix)

    subdomains = [f"{p}.anyitmr.com" for p in prefixes]
    print(f"Total subdomains generated: {len(subdomains)}")

    # 1. Save to .env
    env_file = Path(r"F:\github\turb-gpt-free-register\.env")
    env_text = env_file.read_text(encoding="utf-8")
    subdomains_comma = ",".join(subdomains)
    pattern = r"CLOUDFLARE_DEFAULT_DOMAINS=.*"
    replacement = f'CLOUDFLARE_DEFAULT_DOMAINS="{subdomains_comma}"'
    if re.search(pattern, env_text):
        new_env_text = re.sub(pattern, replacement, env_text)
    else:
        new_env_text = env_text + f"\n{replacement}\n"
    env_file.write_text(new_env_text, encoding="utf-8")
    print("Updated .env successfully.")

    # 2. Output Cloudflare Worker JSON array
    all_domains = ["anyitmr.com"] + subdomains
    worker_domains_json = json.dumps(all_domains, ensure_ascii=False)
    out_json_path = Path(r"F:\github\turb-gpt-free-register\cf_worker_domains.json")
    out_json_path.write_text(worker_domains_json, encoding="utf-8")
    print(f"Saved Cloudflare Worker DOMAINS array to {out_json_path}")

if __name__ == "__main__":
    main()
