# Copyright (c) 2026 Snowflake Inc. All rights reserved.
# Licensed under the Snowflake Skills License.
# Refer to the LICENSE file in the root of this repository for full terms.

"""Insurance claim routing demo data utilities.

Subcommands:
    load   — Create DEMO_CLAIMS_UNLABELED with 80 pre-generated claim summaries.
    split  — Split DEMO_CLAIMS_LABELED into DEMO_CLAIMS_TRAIN (50) and
             DEMO_CLAIMS_TEST (30), then drop the labeled source table.

Example usage:
    python generate_claims_data.py load  --connection MY_CONN --database TEMP --schema PUBLIC
    python generate_claims_data.py split --connection MY_CONN --database TEMP --schema PUBLIC
"""

from __future__ import annotations

import argparse
import logging
import os

import pandas as pd
from snowflake.snowpark import Session

from custom_ai_function_utils import create_session_from_connection

logger = logging.getLogger(__name__)


def create_table(
    session: Session,
    database: str,
    schema: str,
    table_name: str,
) -> None:
    """Create a table for storing insurance claim demo data.

    Args:
        session: Active Snowpark session.
        database: The database name.
        schema: The schema name.
        table_name: The table name.
    """
    fqn = f"{database}.{schema}.{table_name}"
    sql = f"""
        CREATE TABLE {fqn} (
            CLAIM_SUMMARY VARCHAR,
            INCIDENT_CHANNEL VARCHAR,
            CUSTOMER_SEGMENT VARCHAR
        )
    """
    logger.info(f"Creating table {fqn}...")
    session.sql(sql).collect()


def insert_data(
    session: Session,
    database: str,
    schema: str,
    table_name: str,
    df: pd.DataFrame,
) -> None:
    """Insert data into an insurance claim demo table.

    Args:
        session: Active Snowpark session.
        database: The database name.
        schema: The schema name.
        table_name: The table name.
        df: DataFrame with CLAIM_SUMMARY, INCIDENT_CHANNEL, CUSTOMER_SEGMENT columns.
    """
    fqn = f"{database}.{schema}.{table_name}"
    logger.info(f"Inserting {len(df)} rows into {fqn}...")
    from snowflake.snowpark.types import StructType, StructField, StringType

    sp_schema = StructType([StructField(c, StringType()) for c in df.columns])
    rows = list(df.itertuples(index=False, name=None))
    session.create_dataframe(rows, schema=sp_schema).write.mode("append").save_as_table(
        fqn
    )


# 80 pre-generated claim summaries: (claim_summary, incident_channel, customer_segment)
_CLAIMS_DATA: list[tuple[str, str, str]] = [
    (
        "A customer who opened their policy just eleven days ago called to report that all four wheels and tires were stolen from their 2023 BMW X5 overnight in their apartment complex parking lot, but the complex's security camera footage shows no activity near the vehicle during the timeframe provided. The claimant became evasive when asked for the police report number and mentioned they had recently increased their comprehensive coverage limits the day before the alleged theft occurred.",
        "phone",
        "new_customer",
    ),
    (
        "A customer who opened their policy just eleven days ago called to report that all four wheels and tires were stolen from their 2023 BMW X5 overnight in their apartment complex parking lot, though the complex's security camera footage conveniently shows the area was obscured by a delivery truck during the alleged timeframe. The claimant was unable to provide a police report number and became evasive when asked about prior insurance history or whether the vehicle had any existing mechanical issues.",
        "phone",
        "new_customer",
    ),
    (
        "A customer who opened their policy just eleven days ago called to report that their 2022 BMW X5 was allegedly stolen from a grocery store parking lot overnight, though they were unable to explain why the vehicle was parked there for over twelve hours or provide any surveillance footage from the well-monitored lot. The claimant became evasive when asked about their current financial situation and could not produce the second set of keys for the vehicle.",
        "phone",
        "new_customer",
    ),
    (
        "A customer who opened their policy just eleven days ago has filed a web claim for a total loss of $47,000 in high-end electronics and designer clothing due to an alleged apartment burglary, yet the police report notes no signs of forced entry and the claimant was unable to provide any original receipts or proof of ownership. The listed items appear to significantly exceed what would be expected given the claimant's reported income and modest rental unit.",
        "web",
        "new_customer",
    ),
    (
        "A customer who opened their policy just eleven days ago has filed a web claim for a total loss of $47,000 in high-end electronics and designer clothing from an alleged apartment burglary, yet the police report notes no signs of forced entry and the claimant was unable to provide any original receipts or proof of ownership. The claimant's listed address corresponds to a short-term rental unit, and a prior inquiry reveals two similar theft claims filed with different carriers in the past eighteen months.",
        "web",
        "new_customer",
    ),
    (
        "A customer who opened their policy just eleven days ago is filing a claim through their agent for a total loss house fire that allegedly destroyed $180,000 worth of recently purchased electronics and designer furniture, though neighbors report the home appeared mostly unfurnished prior to the incident. The fire marshal's preliminary report notes multiple points of origin and the absence of any personal items such as photographs, documents, or clothing in the debris.",
        "agent",
        "new_customer",
    ),
    (
        "A customer who opened their policy just eleven days ago is filing a claim through their agent for a total loss house fire that allegedly started from a space heater, though the fire marshal's preliminary report notes the home had been largely emptied of personal belongings prior to the blaze. The claimant is pressing for an expedited payout and has provided receipts for high-value electronics and furniture that appear to have been printed recently, with several listing retailers that have been closed for over two years.",
        "agent",
        "new_customer",
    ),
    (
        "A customer who opened their policy just eleven days ago is filing a claim through their agent for a total loss house fire that occurred while they were conveniently out of town, with neighbors reporting no signs of smoke or fire alarms sounding before the structure was fully engulfed. The agent noted the customer had specifically requested maximum coverage limits and declined any deductible increases during the initial policy setup, and the fire marshal's preliminary report indicates multiple points of origin.",
        "agent",
        "new_customer",
    ),
    (
        "A customer who opened their policy just eleven days ago is filing a claim through their agent for a total loss house fire that occurred while they were conveniently out of town, with neighbors reporting no signs of smoke or fire alarms sounding before the structure was fully engulfed. The agent noted the customer had specifically requested maximum coverage limits and declined any deductible increases during the initial policy setup, and the fire marshal's preliminary report indicates multiple points of origin.",
        "agent",
        "new_customer",
    ),
    (
        "A customer who opened their policy just eleven days ago is filing a claim through their agent for a total loss house fire, but the fire marshal's preliminary report notes the home had been cleared of all personal valuables and family photos prior to the blaze. The claimant is also requesting an expedited payout due to alleged urgent relocation needs, despite having already signed a lease on a new apartment three days before the fire occurred.",
        "agent",
        "new_customer",
    ),
    (
        "A customer who opened their policy just nine days ago has filed a web claim for a total loss of $47,000 in high-end electronics and designer clothing due to an alleged apartment burglary, yet the police report notes no signs of forced entry and the claimant was unable to provide any original receipts or proof of ownership. The listed items appear to significantly exceed what would be expected given the claimant's reported income and modest rental unit.",
        "web",
        "new_customer",
    ),
    (
        "A customer who opened their policy just nine days ago is filing a claim through our web portal for a supposedly stolen $8,200 mountain bike from their locked garage, but the police report was filed three days after the alleged theft and the customer cannot provide any purchase receipt or proof of ownership beyond a single blurry photo.",
        "web",
        "new_customer",
    ),
    (
        "A deer ran into the side of my 2024 Honda CR-V while I was driving on Route 9 last Thursday evening, shattering the rear passenger window and leaving significant dent damage along the right quarter panel. I just purchased this vehicle three weeks ago and set up my policy at that time, and my agent suggested I file this claim right away.",
        "agent",
        "new_customer",
    ),
    (
        "A new policyholder called to report that a severe hailstorm two days ago shattered three skylights and cracked multiple roof tiles on her recently purchased townhome, resulting in water intrusion that damaged the upstairs hallway ceiling and a bedroom closet. She noted that she had just activated her homeowners policy three weeks prior and has already contacted a roofing contractor for a temporary tarp installation.",
        "phone",
        "new_customer",
    ),
    (
        "A new policyholder called to report that a severe hailstorm two days ago shattered three skylights and cracked multiple roof tiles on her recently purchased townhome, resulting in water intrusion that damaged the upstairs hallway ceiling and a bedroom closet. She noted that she had just activated her homeowners policy three weeks prior and wanted to understand the claims process before scheduling any emergency repairs.",
        "phone",
        "new_customer",
    ),
    (
        "A new policyholder called to report that a severe hailstorm two days ago shattered three skylights and cracked multiple roof tiles on her recently purchased townhome, resulting in water intrusion that damaged the upstairs hallway ceiling and a bedroom closet. She noted that she had just activated her homeowners policy three weeks prior and wanted to understand the claims process, as this is her first time filing.",
        "phone",
        "new_customer",
    ),
    (
        "A new policyholder called to report that a severe hailstorm two days ago shattered three skylights and cracked multiple roof tiles on her recently purchased townhome, resulting in water intrusion that damaged the upstairs hallway ceiling and a bedroom closet. She noted that she had just activated her homeowners policy three weeks prior and wanted to understand the claims process, as this is her first time filing.",
        "phone",
        "new_customer",
    ),
    (
        "A new policyholder called to report that a severe hailstorm two days ago shattered three skylights and damaged the roof shingles on her recently purchased townhome, resulting in water intrusion that warped the hardwood flooring in the upstairs hallway and master bedroom. She noted that she had only activated her homeowners policy eleven days prior to the storm and is requesting an adjuster visit as soon as possible.",
        "phone",
        "new_customer",
    ),
    (
        "A new policyholder called to report that his detached garage was broken into overnight, with thieves stealing a riding lawn mower, a set of power tools, and two mountain bikes valued at approximately $4,800 total. The customer noted he discovered the break-in this morning when he saw the garage's side door had been pried open and has already filed a police report.",
        "phone",
        "new_customer",
    ),
    (
        "A new policyholder called to report that his detached garage was broken into overnight, with thieves stealing a riding lawn mower, a set of power tools, and two mountain bikes valued at approximately $4,800 total. The customer noted he discovered the break-in this morning when he saw the garage's side door had been pried open and has already filed a police report.",
        "phone",
        "new_customer",
    ),
    (
        "A new policyholder called to report that his detached garage was broken into overnight, with thieves stealing a riding lawn mower, a set of power tools, and two mountain bikes valued at approximately $4,800 total. The customer noted he discovered the break-in this morning when he saw the garage's side door had been pried open and has already filed a police report.",
        "phone",
        "new_customer",
    ),
    (
        "A new policyholder called to report that his detached garage was broken into overnight, with thieves stealing a riding lawn mower, a set of power tools, and two mountain bikes valued at approximately $4,800 total. The customer noted he discovered the break-in this morning when he saw the garage's side door had been pried open and immediately filed a police report before contacting us.",
        "phone",
        "new_customer",
    ),
    (
        "A new policyholder called to report that his detached garage was broken into overnight, with thieves stealing a riding lawn mower, a set of power tools, and two mountain bikes valued at approximately $4,800 total. The customer noted he had only activated his homeowners policy eleven days prior and discovered the break-in when he noticed the garage's side door had been pried open this morning.",
        "phone",
        "new_customer",
    ),
    (
        "A new policyholder called to report that his detached garage was broken into overnight, with thieves stealing a riding lawn mower, a set of power tools, and two mountain bikes valued at approximately $4,800 total. The customer noted he had only activated his homeowners policy eleven days prior and discovered the break-in when he noticed the garage's side door had been pried open this morning.",
        "phone",
        "new_customer",
    ),
    (
        "A new policyholder called to report that she slipped on a wet floor at a grocery store three days ago, sustaining a fractured wrist and severe bruising to her left hip, and is seeking coverage for emergency room costs and follow-up orthopedic appointments. She mentioned that the store manager acknowledged the spill had not been cleaned up despite a prior customer complaint, and she has obtained a copy of the incident report filed at the location.",
        "phone",
        "new_customer",
    ),
    (
        "A new policyholder called to report that she slipped on a wet floor at a grocery store three days ago, sustaining a fractured wrist and severe bruising to her left hip, and is seeking coverage for emergency room costs and follow-up orthopedic appointments. She mentioned that the store manager acknowledged the spill had not been cleaned up despite being reported by another customer twenty minutes prior to her fall.",
        "phone",
        "new_customer",
    ),
    (
        "A new policyholder called to report that she sustained a fractured collarbone and severe bruising after a ceiling-mounted light fixture fell on her while dining at a restaurant last Saturday evening. She is seeking coverage for emergency room treatment, follow-up orthopedic visits, and lost wages during her expected six-week recovery period.",
        "phone",
        "new_customer",
    ),
    (
        "A new policyholder called to report that she sustained a fractured collarbone and severe bruising after a ceiling-mounted light fixture fell on her while dining at a restaurant last Saturday evening. She is seeking coverage for emergency room treatment, follow-up orthopedic visits, and lost wages during her expected six-week recovery period.",
        "phone",
        "new_customer",
    ),
    (
        "A new policyholder called to report that she sustained a fractured collarbone and severe bruising after a ceiling-mounted light fixture fell on her while dining at a restaurant last Saturday evening. She is seeking coverage for emergency room treatment, follow-up orthopedic visits, and lost wages during her expected six-week recovery period.",
        "phone",
        "new_customer",
    ),
    (
        "A new policyholder called to report that she sustained a fractured collarbone and severe bruising after a ceiling-mounted light fixture fell on her while dining at a restaurant last Saturday evening. She is seeking coverage for emergency room treatment, follow-up orthopedic visits, and lost wages during her expected six-week recovery period.",
        "phone",
        "new_customer",
    ),
    (
        "A new policyholder contacted their agent three weeks after binding coverage to report that a severe hailstorm had shattered two skylights and damaged the roof shingles on their recently purchased colonial home, resulting in water intrusion that warped the hardwood flooring in the upstairs hallway and master bedroom. The agent filed the claim on the customer's behalf and arranged for an emergency tarp service to prevent further water damage until an adjuster could inspect the property.",
        "agent",
        "new_customer",
    ),
    (
        "A new policyholder contacted their agent three weeks after binding coverage to report that a severe hailstorm had shattered two skylights and damaged the roof shingles on their recently purchased home, resulting in water intrusion that warped the hardwood flooring in the upstairs hallway and master bedroom. The agent filed the claim on the customer's behalf and arranged for an emergency tarp service to prevent further water damage until an adjuster could inspect the property.",
        "agent",
        "new_customer",
    ),
    (
        "A new policyholder contacted their agent three weeks after binding coverage to report that a severe hailstorm had shattered two skylights and damaged the roof shingles on their recently purchased home, resulting in water intrusion that warped the hardwood flooring in the upstairs hallway and master bedroom. The agent filed the claim on the customer's behalf and arranged for an emergency tarp service to prevent further water damage until an adjuster could inspect the property.",
        "agent",
        "new_customer",
    ),
    (
        "A new policyholder contacted their agent three weeks after binding coverage to report that a severe hailstorm had shattered two skylights and damaged the roof shingles on their recently purchased home, resulting in water intrusion that warped the hardwood flooring in the upstairs hallway. The agent filed the claim on the customer's behalf and arranged for an emergency tarp service to prevent further water damage until an adjuster could inspect the property.",
        "agent",
        "new_customer",
    ),
    (
        "A new policyholder contacted their agent to report that a severe hailstorm damaged the roof, gutters, and exterior siding of their recently purchased home, with water now leaking into the upstairs bedroom through compromised shingles. The agent filed the claim on the customer's behalf and arranged for an emergency tarp installation to prevent further interior water damage while an adjuster is scheduled.",
        "agent",
        "new_customer",
    ),
    (
        "A new policyholder emailed to file a claim after a three-vehicle chain-reaction collision on I-95, but the police report has not yet been released and the other two drivers' insurance information was not exchanged at the scene due to the confusion. The customer is requesting guidance on how to proceed without the accident report and is unable to confirm which of the other parties' insurers should be contacted for subrogation.",
        "email",
        "new_customer",
    ),
    (
        "A new policyholder emailed to file a claim for water damage to their recently purchased condominium, but the closing documents, proof of ownership transfer, and the original home inspection report have not yet been received from the title company, delaying verification of coverage effective dates. The claim is further complicated by a dispute between the condo association's master policy insurer and the individual unit owner's carrier over which party is responsible for damage to shared plumbing infrastructure that caused the leak.",
        "email",
        "new_customer",
    ),
    (
        "A new policyholder emailed to file a claim for water damage to their recently purchased condominium, but the closing documents, proof of ownership transfer, and the original home inspection report have not yet been received from the title company, delaying verification of coverage effective dates. The claim is further complicated by a dispute between the condo association's master policy insurer and the individual unit owner's carrier over which party is responsible for damage to shared plumbing infrastructure within the walls.",
        "email",
        "new_customer",
    ),
    (
        "A new policyholder emailed to file a liability claim after a three-vehicle chain-reaction collision in a parking garage, but the police report was never completed at the scene and the third driver's insurance information was not exchanged, leaving critical documentation gaps that are complicating fault determination. The customer has provided their own written account and dashcam footage but is requesting guidance on how to proceed without the missing third-party details.",
        "email",
        "new_customer",
    ),
    (
        "A new policyholder emailed to file a liability claim after a three-vehicle chain-reaction collision in a parking garage, but the police report was never completed at the scene and the third driver's insurance information was not exchanged, leaving critical documentation gaps that are complicating fault determination. The customer has provided their own written account and dashcam footage but is requesting guidance on how to proceed without the missing third-party details.",
        "email",
        "new_customer",
    ),
    (
        "A new policyholder emailed to file a liability claim after a three-vehicle chain-reaction collision in a shopping center parking lot, but the police report was never completed at the scene and the third driver's insurance information was not exchanged. The customer is requesting guidance on how to proceed without the missing documentation and is unsure which of the three parties' insurers should be handling the property damage assessments.",
        "email",
        "new_customer",
    ),
    (
        "A new policyholder emailed to report a three-vehicle chain-reaction collision on Interstate 40, involving drivers insured by three different carriers, but was unable to provide the police report number or the other parties' insurance details as the responding officer only issued a verbal case reference at the scene. The customer is requesting guidance on how to proceed with the claim given that the accident report from the county sheriff's office has a 10-14 business day processing delay and liability determination requires coordination among all three insurers.",
        "email",
        "new_customer",
    ),
    (
        "A new policyholder emailed to report a three-vehicle chain-reaction collision on Interstate 40, involving drivers insured by three different carriers, but was unable to provide the police report number or the other parties' insurance details as the responding officer only issued a verbal case reference at the scene. The customer is requesting guidance on how to proceed with the claim given that the police department has informed them the written accident report will not be available for 10-14 business days.",
        "email",
        "new_customer",
    ),
    (
        "A new policyholder reported through the online claims portal that their detached garage was broken into overnight, with thieves stealing a riding lawn mower, a set of power tools, and two mountain bikes valued at approximately $4,800 total. The customer noted that the garage lock had been cut with bolt cutters and provided a police report filed earlier that morning.",
        "web",
        "new_customer",
    ),
    (
        "A new policyholder reported through their agent that their detached garage was broken into overnight, with thieves stealing a riding lawn mower, a set of power tools, and two mountain bikes valued at approximately $4,800 total. The police report indicates forced entry through a side door, and the customer noted they had only activated their homeowners policy three weeks prior to the incident.",
        "agent",
        "new_customer",
    ),
    (
        "A new policyholder reported through their agent that their detached garage was broken into overnight, with thieves stealing a riding lawn mower, a set of power tools, and two mountain bikes valued at approximately $4,800 total. The police report indicates forced entry through a side door, and the customer noted they had only activated their homeowners policy three weeks prior to the incident.",
        "agent",
        "new_customer",
    ),
    (
        "A new policyholder reported through their agent that their detached garage was broken into overnight, with thieves stealing a riding lawn mower, a set of power tools, and two mountain bikes valued at approximately $4,800 total. The police report indicates forced entry through a side door, and the customer noted they had only activated their homeowners policy three weeks prior to the incident.",
        "agent",
        "new_customer",
    ),
    (
        "A new policyholder reported through their agent that their detached garage was broken into overnight, with thieves stealing a riding lawn mower, a set of power tools, and two mountain bikes valued at approximately $4,800 total. The police report indicates forced entry through a side door, and the customer noted they had only activated their homeowners policy three weeks prior to the incident.",
        "agent",
        "new_customer",
    ),
    (
        "A new policyholder reported through their agent that their detached garage was broken into overnight, with thieves stealing a riding lawn mower, a set of power tools, and two mountain bikes valued at approximately $4,800 total. The police report indicates forced entry through a side door, and the customer noted they had only activated their homeowners policy three weeks prior to the incident.",
        "agent",
        "new_customer",
    ),
    (
        "A new policyholder reported through their agent that their detached garage was broken into overnight, with thieves stealing a riding lawn mower, a set of power tools, and two mountain bikes valued at approximately $4,800 total. The police report indicates forced entry through a side door, and the customer noted they had only activated their homeowners policy three weeks prior to the incident.",
        "agent",
        "new_customer",
    ),
    (
        "A new policyholder reported through their agent that their detached garage was broken into overnight, with thieves stealing a riding lawn mower, a set of power tools, and two mountain bikes valued at approximately $4,800 total. The police report indicates the padlock was cut and security camera footage from a neighbor's property captured an unidentified individual loading items into a dark-colored pickup truck.",
        "agent",
        "new_customer",
    ),
    (
        "A new policyholder submitted a web claim reporting that a severe hailstorm damaged the roof and skylights of their recently purchased home, resulting in water intrusion that warped hardwood flooring in the upstairs hallway and master bedroom. The customer noted they had only activated their homeowners policy three weeks prior and included timestamped photos of the damage along with a local weather service alert confirming the storm.",
        "web",
        "new_customer",
    ),
    (
        "A new policyholder submitted a web claim reporting that a severe hailstorm damaged the roof and skylights of their recently purchased home, resulting in water intrusion that warped hardwood flooring in the upstairs hallway and master bedroom. The customer noted they had only activated their homeowners policy three weeks prior to the storm event.",
        "web",
        "new_customer",
    ),
    (
        "A new policyholder submitted a web claim reporting that a severe hailstorm damaged the roof and skylights of their recently purchased home, resulting in water intrusion that warped hardwood floors in the upstairs hallway and master bedroom. The customer noted they had only activated their homeowners policy three weeks prior and included timestamped photos of the damage along with a local weather service alert confirming the storm.",
        "web",
        "new_customer",
    ),
    (
        "A new policyholder was struck by a falling shelf unit at a home improvement store three weeks after enrolling in a personal injury protection plan, sustaining a fractured collarbone and deep lacerations to the left arm requiring surgical intervention. The claim was filed through the customer's assigned agent, who documented the incident report and emergency room records for processing.",
        "agent",
        "new_customer",
    ),
    (
        "A new policyholder was struck by a falling shelf unit at a home improvement store three weeks after enrolling in a personal injury protection plan, sustaining a fractured collarbone and severe bruising to the left shoulder. The claim was filed through the customer's assigned agent, who documented the incident report and emergency room records from the treating hospital.",
        "agent",
        "new_customer",
    ),
    (
        "A new policyholder was struck by a falling shelf unit at a home improvement store three weeks after enrolling in a personal injury protection plan, sustaining a fractured collarbone and severe bruising to the left shoulder. The claim was filed through the customer's assigned insurance agent, who documented the incident report and emergency room records from the treating hospital.",
        "agent",
        "new_customer",
    ),
    (
        "A new policyholder was struck by a falling shelf unit at a home improvement store three weeks after obtaining their policy, sustaining a fractured collarbone and severe bruising to the left shoulder. The claim was filed through their insurance agent, who documented the incident report and emergency room records for the bodily injury liability review.",
        "agent",
        "new_customer",
    ),
    (
        "A new policyholder's 2023 Kia Sportage was rear-ended at a red light on Route 9 by a distracted driver, resulting in a crushed rear bumper, damaged tailgate, and misaligned rear axle. The claim was filed through their assigned agent the following morning, and the customer reported persistent neck stiffness from the impact.",
        "agent",
        "new_customer",
    ),
    (
        "A policy purchased online just eleven days ago is now the subject of a claim by the new policyholder, who reports that their recently acquired 2023 BMW X5 was stolen from a grocery store parking lot overnight, despite the vehicle's GPS tracker showing no movement from the insured's home address during the alleged timeframe. The claimant has been unable to provide a second key fob and became evasive when asked about the vehicle's current loan balance, which our preliminary check reveals exceeds the car's market value by approximately $14,000.",
        "web",
        "new_customer",
    ),
    (
        "A policy purchased online just eleven days ago is now the subject of a claim by the new policyholder, who reports that their recently acquired 2023 luxury watch collection, valued at $47,000, was stolen from an unlocked vehicle while parked overnight in their driveway, with no surveillance footage available despite the claimant mentioning a home security system during the application process. The claimant has been unable to provide original purchase receipts and instead submitted only screenshots of online listings for similar items.",
        "web",
        "new_customer",
    ),
    (
        "A premium policyholder submitted a web claim for $187,000 in water damage to their recently renovated lakefront property, but the adjuster noted the homeowner had tripled their coverage limits just eleven days before the alleged pipe burst and neighbors reported seeing the residence unoccupied for several weeks. The claimant's contractor brother-in-law provided the sole repair estimate, which included replacement of items not typically affected by the described ground-floor plumbing failure.",
        "web",
        "premium",
    ),
    (
        "A premium policyholder submitted a web claim for $187,000 in water damage to their recently renovated lakefront property, but the adjuster noted the homeowner had tripled their coverage limits just eleven days before the alleged pipe burst, and the plumber's invoice appears to reference work completed two weeks prior to the reported loss date. Neighbors also reported seeing the claimant loading furniture into a rental truck the evening before the incident was supposedly discovered.",
        "web",
        "premium",
    ),
    (
        "A premium policyholder submitted a web claim for $187,000 in water damage to their recently renovated lakefront property, but the adjuster noted the homeowner had tripled their coverage limits just eleven days before the alleged pipe burst, and the plumber's invoice appears to reference work completed two weeks prior to the reported loss date. Neighbors also reported seeing the claimant loading furniture into a rental truck the evening before the incident was supposedly discovered.",
        "web",
        "premium",
    ),
    (
        "A premium policyholder submitted a web claim for $187,000 in water damage to their recently renovated lakefront property, but the adjuster noted the homeowner had tripled their coverage limits just eleven days before the alleged pipe burst, and the plumber's invoice appears to reference work completed two weeks prior to the reported loss date. Neighbors also reported seeing the claimant loading furniture into a rental truck the evening before the incident was supposedly discovered.",
        "web",
        "premium",
    ),
    (
        "A premium policyholder submitted a web claim for $187,000 in water damage to their recently renovated lakefront property, but the adjuster noted the homeowner had tripled their coverage limits just eleven days before the alleged pipe burst, and the plumber's invoice appears to reference work completed two weeks prior to the reported loss date. Neighbors also reported seeing the claimant loading furniture into a rental truck the evening before the incident was supposedly discovered.",
        "web",
        "premium",
    ),
    (
        "After last Thursday's severe thunderstorm, a large oak tree in our neighbor's yard was uprooted and crashed through our garage roof, crushing one vehicle inside and causing extensive structural damage to the attached mudroom. We just purchased this home six weeks ago and activated our policy on closing day, and I'm submitting photos and the initial contractor assessment via this email for your review.",
        "email",
        "new_customer",
    ),
    (
        "After last Thursday's severe thunderstorm, a large oak tree in our neighbor's yard was uprooted and crashed through our garage roof, crushing one vehicle inside and causing extensive structural damage to the garage and adjacent mudroom. We purchased our homeowner's policy just six weeks ago when we closed on the house, and I'm submitting photos of the damage along with the emergency tarp installation receipt from the contractor we called that evening.",
        "email",
        "new_customer",
    ),
    (
        "After last week's severe thunderstorm brought down a large oak tree onto our garage roof, we discovered extensive structural damage to the garage and water intrusion into the attached mudroom. We purchased our homeowner's policy just six weeks ago and are submitting this claim via email along with photos of the damage and a preliminary estimate from a local contractor.",
        "email",
        "new_customer",
    ),
    (
        "After last week's severe thunderstorm brought down a large oak tree onto our garage roof, we discovered extensive structural damage to the garage and water intrusion into the attached mudroom; we just purchased this home and closed on it three weeks ago. I've attached photos of the damage and the initial estimate from a local contractor to this email for your review.",
        "email",
        "new_customer",
    ),
    (
        "After last week's severe thunderstorm knocked a large oak tree onto our garage roof, we discovered extensive structural damage to the garage and water intrusion into the attached mudroom that has warped the hardwood flooring and soaked through the drywall. We purchased our homeowner's policy just six weeks ago and are submitting this claim via email along with photos and a preliminary estimate from our contractor.",
        "email",
        "new_customer",
    ),
    (
        "Called in to report that my parked car was struck by a delivery truck backing out of a loading zone at the shopping center on Elm Street yesterday afternoon, crushing the rear bumper and shattering the taillight assembly. I just started this policy three weeks ago and the truck driver's company is disputing fault, so I need to understand how my coverage applies.",
        "phone",
        "new_customer",
    ),
    (
        "Called in to report that my parked car was struck by a delivery truck backing out of a loading zone at the shopping center on Elm Street yesterday afternoon, resulting in a crushed rear bumper and shattered taillight on my 2024 Hyundai Tucson. I just purchased the vehicle three weeks ago and activated my policy on the same day, so this is my first time filing a claim.",
        "phone",
        "new_customer",
    ),
    (
        "Called in to report that my parked car was struck by a delivery truck backing out of a loading zone at the shopping center on Elm Street yesterday afternoon, resulting in significant damage to the rear bumper and tailgate of my 2023 Honda CR-V. I just purchased the policy last week and this is my first time filing a claim, so I'm not sure what documentation you'll need from me.",
        "phone",
        "new_customer",
    ),
    (
        "Called in to report that my parked car was struck by a delivery truck backing out of a neighboring driveway yesterday afternoon, crushing the driver's side rear quarter panel and shattering the taillight. I just started my policy three weeks ago and this is my first time filing any kind of insurance claim, so I'm not sure what documentation you'll need from me.",
        "phone",
        "new_customer",
    ),
    (
        "Called to report that my apartment was broken into while I was at work yesterday; the back door lock was forced open and my laptop, gaming console, two designer watches, and approximately $300 in cash were taken from the bedroom. I just started this policy three weeks ago and haven't had a chance to do a home inventory yet, but I do have receipts for most of the electronics.",
        "phone",
        "new_customer",
    ),
    (
        "Caller reported that while backing out of a grocery store parking space yesterday evening, she struck a concrete bollard she didn't see in her rearview mirror, cracking her rear bumper and damaging the taillight assembly on her 2024 Hyundai Tucson. As a new policyholder who just activated her coverage three weeks ago, she wanted to confirm her deductible amount and understand the next steps for filing her first claim.",
        "phone",
        "new_customer",
    ),
    (
        "Caller reported that while backing out of a grocery store parking space yesterday evening, she struck a concrete bollard she didn't see in her side mirror, cracking the rear bumper and denting the quarter panel of her 2024 Kia Sportage. As a new policyholder who just activated her coverage three weeks ago, she wanted to confirm her deductible amount and understand the next steps for filing her first claim.",
        "phone",
        "new_customer",
    ),
    (
        "Caller reported that while driving through a construction zone on Route 9, a piece of unsecured rebar flew off a flatbed truck ahead and punctured the front grille and radiator of her 2021 Hyundai Tucson, causing the engine to overheat and requiring an immediate tow. She obtained the truck's license plate number and has dashcam footage of the incident, and is requesting coverage for towing, radiator replacement, and a rental vehicle while repairs are completed.",
        "phone",
        "standard",
    ),
    (
        "Caller reported that while driving through a construction zone on Route 9, a piece of unsecured rebar flew off a flatbed truck ahead and punctured the front grille and radiator of her 2021 Hyundai Tucson, causing the engine to overheat and requiring an immediate tow. She stated she was able to capture dashcam footage of the incident and has already obtained a repair estimate of $4,200 from her local body shop.",
        "phone",
        "standard",
    ),
]


def get_unlabeled_claim_rows() -> list[tuple[str, str, str]]:
    """Return embedded unlabeled claims (same rows ``load`` writes to ``DEMO_CLAIMS_UNLABELED``)."""
    return list(_CLAIMS_DATA)


def _connect(connection: str) -> Session:
    """Open a Snowpark session using the given connection name."""
    name = os.getenv("SNOWFLAKE_CONNECTION_NAME") or connection
    logger.info(f"Connecting to Snowflake using connection '{name}'...")
    return create_session_from_connection(name)


def load(connection: str, database: str, schema: str) -> None:
    """Create DEMO_CLAIMS_UNLABELED from embedded seed data."""
    df = pd.DataFrame(
        get_unlabeled_claim_rows(),
        columns=["CLAIM_SUMMARY", "INCIDENT_CHANNEL", "CUSTOMER_SEGMENT"],
    )
    session = _connect(connection)
    try:
        create_table(session, database, schema, "DEMO_CLAIMS_UNLABELED")
        insert_data(session, database, schema, "DEMO_CLAIMS_UNLABELED", df)
        logger.info(
            f"  Created {database}.{schema}.DEMO_CLAIMS_UNLABELED ({len(df)} rows)"
        )
    finally:
        session.close()


def split(connection: str, database: str, schema: str) -> None:
    """Split DEMO_CLAIMS_LABELED into TRAIN (50) and TEST (30), then drop it."""
    fqn_labeled = f"{database}.{schema}.DEMO_CLAIMS_LABELED"
    fqn_train = f"{database}.{schema}.DEMO_CLAIMS_TRAIN"
    fqn_test = f"{database}.{schema}.DEMO_CLAIMS_TEST"

    session = _connect(connection)
    try:
        logger.info(f"Creating {fqn_train} (50 rows)...")
        session.sql(f"""
            CREATE TABLE {fqn_train} AS
            SELECT CLAIM_SUMMARY, INCIDENT_CHANNEL, CUSTOMER_SEGMENT,
                   EXPECTED:claim_route::STRING AS EXPECTED_OUTPUT,
                   EXPECTED::STRING AS EXPECTED_JSON
            FROM {fqn_labeled}
            LIMIT 50
        """).collect()

        logger.info(f"Creating {fqn_test} (30 rows)...")
        session.sql(f"""
            CREATE TABLE {fqn_test} AS
            SELECT CLAIM_SUMMARY, INCIDENT_CHANNEL, CUSTOMER_SEGMENT,
                   EXPECTED:claim_route::STRING AS EXPECTED_OUTPUT,
                   EXPECTED::STRING AS EXPECTED_JSON
            FROM {fqn_labeled}
            LIMIT 30 OFFSET 50
        """).collect()

        logger.info(f"Dropping {fqn_labeled}...")
        session.sql(f"DROP TABLE IF EXISTS {fqn_labeled}").collect()

        # Verify counts
        train_n = session.sql(f"SELECT COUNT(*) FROM {fqn_train}").collect()[0][0]
        test_n = session.sql(f"SELECT COUNT(*) FROM {fqn_test}").collect()[0][0]
        logger.info(f"  {fqn_train}: {train_n} rows")
        logger.info(f"  {fqn_test}: {test_n} rows")
    finally:
        session.close()


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    """Add --connection, --database, --schema to a subparser."""
    parser.add_argument("--connection", required=True, help="Snowflake connection name")
    parser.add_argument("--database", required=True, help="Target database")
    parser.add_argument("--schema", required=True, help="Target schema")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Insurance claim routing demo data utilities."
    )
    subs = parser.add_subparsers(dest="command", required=True)

    _add_common_args(subs.add_parser("load", help="Create DEMO_CLAIMS_UNLABELED"))
    _add_common_args(subs.add_parser("split", help="Split labeled → train/test"))

    args = parser.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )

    if args.command == "load":
        load(args.connection, args.database, args.schema)
    elif args.command == "split":
        split(args.connection, args.database, args.schema)
