# Official Rules

**Build with DataHub: The Agent Hackathon (the "Hackathon") Official Rules**

> NO PURCHASE OR PAYMENT NECESSARY TO ENTER OR WIN. A PURCHASE OR PAYMENT WILL NOT INCREASE YOUR CHANCES OF WINNING.
>
> SUBMISSION OF ANY ENTRY CONSTITUTES AGREEMENT TO THESE OFFICIAL RULES AS A CONTRACT BETWEEN ENTRANT (AND EACH INDIVIDUAL MEMBER OF ENTRANT), THE HACKATHON SPONSOR, AND DEVPOST.

---

## 1. Dates and Timing

- **Registration & Submission Period:** July 6, 2026 (09:00 am Eastern Time) - August 10, 2026 (05:00 pm Eastern Time).
- **Feedback Period:** July 6, 2026 (09:00 am Eastern Time) - August 10, 2026 (05:00 pm Eastern Time).
- **Judging Period:** August 17, 2026 (10:00 am Eastern Time) - August 31, 2026 (5:00 pm Eastern Time).
- **Winners Announced:** On or around September 8, 2026 (2:00 pm Eastern Time).

## 2. Sponsor and Administrator

- **Sponsor:** DataHub, 3101 Park Boulevard, Palo Alto, CA 94306, United States.
- **Administrator:** Devpost, Inc. ("Devpost"), 250 Broadway, Floor 24, New York, NY 10007.

## 3. Eligibility

**The Hackathon IS open to:**
- Individuals who are at least 18 years old or have reached the age of majority where they reside as of the time of entry ("Eligible Individuals").
- Teams of Eligible Individuals ("Teams").
- Organizations (corporations, not-for-profit corporations and other nonprofit organizations, LLCs, partnerships, and other legal entities) that exist and have been organized or incorporated at the time of entry.

(collectively, "Entrants")

An Eligible Individual may join more than one Team or Organization, and an individual who is part of a Team/Organization may also enter individually. A Team or Organization must appoint and authorize one individual (the "Representative") to represent, act, and enter a Submission on their behalf. By entering on behalf of a Team/Organization you represent and warrant you are the authorized Representative.

**The Hackathon IS NOT open to:**
- Individuals resident in, or Organizations domiciled in, a country/state/province/territory where US or local law prohibits participating or receiving a prize (including but not limited to **Brazil, Quebec, Russia, Crimea, Cuba, Iran, and North Korea**, and any other country designated by the US Treasury's Office of Foreign Assets Control).
- Organizations involved with the design, production, paid promotion, execution, or distribution of the Hackathon, including the Sponsor and Administrator ("Promotion Entities").
- Employees, representatives, and agents\*\* of such Promotion Entities, and all members of their immediate family or household\*.
- Any other individual involved with the design, production, promotion, execution, or distribution of the Hackathon, and each member of their immediate family or household\*.
- Any Judge, or company or individual that employs a Judge.
- Any parent company, subsidiary, or other affiliate\*\*\* of any organization described above.
- Any other individual or organization whose participation would create, in the sole discretion of Sponsor and/or Administrator, a real or apparent conflict of interest.

> \* **Immediate family** includes spouse, children and stepchildren, parents and stepparents, and siblings and stepsiblings. **Household** includes any other person sharing the same residence for at least three (3) months out of the year.
>
> \*\* **Agents** include individuals or organizations that, in creating a Submission, are acting on behalf of and at the direction of a Promotion Entity through a contractual or similar relationship.
>
> \*\*\* An **affiliate** is: (a) an organization under common control, sharing a common majority or controlling owner, or common management; or (b) an organization that has a substantial ownership in, or is substantially owned by, the other organization.

## 4. How To Enter

Entrants may enter by visiting **datahub.devpost.com** ("Hackathon Website") and following these steps:

1. **Register** on the Hackathon Website by clicking "Join Hackathon." Sign up for a free Devpost account or log in with an existing one. This enables important updates and Submission creation.
2. Obtain access to the required developer tools/platform and complete a Project (see Project Requirements). Use of developer tools is subject to the related license agreement. Entry constitutes consent for Sponsor and Devpost to collect and maintain an entrant's personal information for operating and publicizing the Hackathon.
3. Spin up DataHub locally in minutes with the Quickstart Guide.
4. **Most Valuable Feedback bonus prizes:** To be considered, Entrants must be registered on Devpost and complete an online form (each a "Feedback Submission"). Feedback must be submitted during the Feedback Period. To be eligible, the Feedback Submission must be complete with actionable comments DataHub can use to improve the SDKs or related documentation (e.g., bug reports, UI improvements, suggested integrations, etc.). **One Feedback Submission per Entrant.**
5. Complete and enter all required fields on the "Enter a Submission" page (each a "Submission") during the Submission Period, following the requirements below.

### Project Requirements

**What to Create:** Entrants must create a working software application that uses DataHub to solve one of the Challenge Categories below. Projects must incorporate DataHub by using the open-source platform **together with at least one** of: the **MCP Server, Agent Context Kit, DataHub Skills, or Analytics Agent**.

**Challenge Categories** (full text - see also `02-challenges.md`):

1. **Agents That Do Real Work:** Build AI agents that handle data problems on their own - alone or as a team. Your agent reads DataHub through the MCP Server or Agent Context Kit to understand what's connected to what, takes action, and writes results back so the next person or agent inherits the knowledge.
2. **Metadata-Aware Code Generation & Development:** Build agents that generate production data code - transformation models, pipeline DAGs (Airflow, Prefect, Dagster), ingestion scripts, helper scripts, configurations, migration code - that works on the first try because they use DataHub Skills or the MCP Server to read DataHub for the real schemas, lineage, and rules before generating anything. The artifact lives in a Git repo, goes into a PR, and your data team would actually merge it. Strong submissions include sample generated artifacts so judges can see the quality of the output.
3. **Production ML Agents:** Build agents for ML teams that protect models in production. Use DataHub's end-to-end ML lineage - the path from training data to feature to model to deployment - accessed via the Agent Context Kit or MCP Server to catch silent problems that can break ML systems before they cost money.
4. **Open / Wildcard:** Build anything creative that uses DataHub as the foundation - supply chain optimization, financial forecasting, regulatory automation, knowledge capture, or anything else. Use whatever fits from DataHub's open-source stack (MCP Server, Agent Context Kit, DataHub Skills, Analytics Agent, or any other DataHub product).

(each a "Project")

- **Functionality:** The Project must install and run consistently on the intended platform and function as depicted in the video and/or text description.
- **Platforms:** A submitted Project must run on the platform specified in the Submission Requirements.
- **New Projects Only:** Projects must be newly created during the Submission Period. Participants may use standard development tools (frameworks, libraries, starter templates, AI coding assistants) but must disclose any other pre-existing code or work incorporated. The work described and submitted must have been built during the Submission Period.
- **Third Party Integrations:** If a Project integrates any third-party SDK, APIs, and/or data, Entrant must be authorized to use them per applicable terms/licensing.

### Submission Requirements

Submissions must meet the following:

- Include a Project built with the required developer tools that meets the Project Requirements. Provide a URL to your Project for easy judge access/testing.
- Provide a URL to your public code repository for judging and testing. It must contain all necessary source code, assets, and full instructions for the project to be functional. It must be public and open source by including an **Apache 2.0 open source license file**, detectable and visible at the top of the repository page (in the About section).
- Include a text description summarizing the Project (features, functionality, technologies, data used).
- Include a demonstration video. The video:
  - Should be **less than three (3) minutes** (judges need not watch beyond three minutes).
  - Should show the Project functioning on the device for which it was built.
  - Must be uploaded and made publicly visible on **YouTube, Vimeo, or Youku**, with a link provided on the submission form.
  - Must not include third-party trademarks, or copyrighted music/material unless the Entrant has permission.
- **Should include Sample outputs (recommended):** If the Project generates artifacts (code files, queries, reports, transformations), include examples in the repository (e.g., an `examples/` folder) so judges can evaluate output quality without running it.

**Multiple Submissions:** An Entrant may submit more than one Submission, but each must be unique and substantially different from the Entrant's others (as determined by Sponsor and Devpost in their sole discretion).

**Submission ownership:** Be the original work of the Entrant, solely owned by the Entrant, and not violate the IP rights of any other person or entity.

**Testing:** Access must be provided to a working Project for judging/testing via a link to a website, functioning demo, or test build. If the site is private, include login credentials in testing instructions. The Project must be available free of charge and without restriction for testing, evaluation, and use by Sponsor, Administrator, and Judges until the Judging Period ends. Judges are not required to test and may judge based solely on the text description, images, and video. If the Project runs on proprietary/third-party hardware not widely available to the public (beyond smartphones, tablets, or desktop computers), Sponsor/Administrator may require physical access to the hardware on request.

**Language Requirements:** All Submission materials must be in English, or the Entrant must provide an English translation of the demonstration video, text description, testing instructions, and all other materials.

**Team Representation:** A team/organization must appoint and authorize one Representative (who meets eligibility) to represent, act, and enter a Submission on their behalf.

**Intellectual Property:** Your Submission must: (a) be your (or your Team/Organization's) original work product; (b) be solely owned by you/your Team/Organization with no other person or entity having any right or interest in it; and (c) not violate the IP or other rights (copyright, trademark, patent, contract, privacy) of any other person or entity. An Entrant may contract with a third party for technical assistance provided the Submission components are solely the Entrant's work product and the result of the Entrant's ideas and creativity, and the Entrant owns all rights. An Entrant may use open source software/hardware provided they comply with applicable open source licenses and, as part of the Submission, create software that enhances and builds upon the underlying open source product's features and functionality.

**Financial or Preferential Support:** A Project must not have been developed, or derived from a Project developed, with financial or preferential support from the Sponsor or Administrator (including funding/investment, developed under contract, or a commercial license) any time prior to the end of the Hackathon Submission Period. Sponsor may, at their sole discretion, disqualify a Project if awarding it a prize would create a real or apparent conflict of interest.

## 5. Submission Modifications

**Draft Submissions:** Before the Submission Period ends, you may save draft versions on Devpost to your portfolio before submitting materials for evaluation. Once the Submission Period has ended, you may not change or alter your Submission, but you may continue updating the Project in your Devpost portfolio.

**Modifications After the Submission Period:** Sponsor and Devpost may permit you to modify part of your Submission after the Submission Period only to add, remove, or replace material that potentially infringes a third-party mark or right, discloses personally identifiable information, or is otherwise inappropriate. The modified Submission must remain substantively the same, with only the permitted modification.

## 6. Judges & Criteria

Sponsor and Administrator reserve the sole right to determine eligibility and judging methodologies. This may use expert panels, peer review, automated AI-driven analysis, or any combination. Eligible submissions are evaluated by a panel of Judges selected by the Sponsor. Judges may be employees of the sponsor or third parties, may or may not be listed individually on the Hackathon Website, and may change before or during the Judging Period. Judging may take place in one or more rounds with one or more panels.

**Stage One:** Pass/fail on whether the ideas meet a baseline level of viability - the Project reasonably fits the theme and reasonably applies the required APIs/SDKs.

**Stage Two:** All Submissions that pass Stage One are evaluated on the following **equally weighted** criteria (the "Judging Criteria"), at the judges' sole and absolute discretion:

- **Use of DataHub:** How meaningfully does the project use DataHub - its context graph (lineage, ownership, schemas, ML metadata, governance signals), the MCP Server, Agent Context Kit, DataHub Skills, or Analytics Agent? The strongest submissions go beyond reading metadata and contribute back to the graph where appropriate.
- **Technical Execution:** Quality of implementation, robustness, and whether the project actually works end-to-end. Does the code do what the submission claims?
- **Originality:** How creative and novel is the approach? Submissions should clearly go beyond features DataHub already provides out of the box. Building on top of, extending, or composing shipped features is welcome; rebuilding them as if from scratch isn't.
- **Real-World Usefulness:** Would a real data, ML, or AI platform team see clear value in this? Submissions don't need to be production-ready, but should solve a problem practitioners actually face.
- **Submission Quality:** Quality of the demo video, written description, and README. A judge should be able to understand what the project does, why it matters, and find clear setup instructions to try it themselves.
- **Bonus:** Submissions that include meaningful open-source contributions to DataHub - new connectors, skills, fixes, RFCs, or documentation improvements - will be looked on favorably. Existing contributions extended for the hackathon also count. Optional, but encouraged.

The Judges' scores determine potential winners. Prize-eligible Entrant(s) whose Submissions earn the highest overall scores become potential winners of that prize.

**Feedback Submission Criteria:** Eligible Feedback Submissions are evaluated based on completeness, viability, and potential impact of the feedback.

**Tie Breaking:** For each Prize, if two or more Submissions are tied, the tied Submission with the highest score in the first applicable criterion (in the order listed) is the higher-scoring one. Repeat down the criteria list as needed. If tied on all criteria, the panel of Judges votes on the tied Submissions.

## 7. Intellectual Property Rights

All Submissions remain the IP of the individuals/organizations that developed them. By submitting, entrants grant the Sponsor a non-exclusive license to use the entry for judging. Entrants agree the Sponsor and Devpost may promote the Submission and use the name, likeness, voice, and image of all contributors in materials promoting or publicizing the Hackathon and its results, during the Hackathon Period and for three years thereafter. Some Submission components may be displayed to the public; other materials may be viewed by Sponsor, Devpost, and judges for screening/evaluation. Entrants represent and warrant that (a) submitted content is not copyrighted, trade-secret protected, or subject to third-party IP or other proprietary rights (including privacy/publicity), unless the entrant owns those rights or has permission; and (b) the content contains no viruses, Trojan horses, worms, spyware, or other disabling/harmful/malicious code.

## 8. Prizes

| Winner | Prize | Qty | Eligible Submissions |
|---|---|---|---|
| **Grand Prize** | $6,000 + Presentation at DataHub Townhall + Social media @ Slack community promotion + Special LinkedIn Badge | 1 | All eligible submissions |
| **Challenge Winners** | $3,000 + Social media @ Slack community promotion + Special LinkedIn Badge | 4 | All eligible submissions. One awarded per Challenge category |
| **Honourable Mention** | $1,000 + Special LinkedIn Badge | 2 | All eligible submissions |
| **Most Valuable Feedback Survey Prize** | $50 USD | 10 | All eligible individuals who complete a feedback survey |

**Important notes on multiple prize eligibility:**
- Each Eligible Submission is eligible to win one (1) prize.
- Each individual entrant is eligible for one Most Valuable Feedback Prize. Eligible individuals who only submit Most Valuable Feedback Surveys will NOT be eligible for any additional prizes. Feedback Prizes are awarded to individuals, not Projects, and will not be visible on the platform.

**Substitutions & Changes:** Prizes are non-transferable by the winner. Sponsor may substitute a prize of equivalent or greater value at its sole discretion. No prize is awarded if there are no eligible Submissions/Entrants for that prize.

**Verification Requirement:** THE AWARD OF A PRIZE IS SUBJECT TO VERIFICATION OF THE IDENTITY, QUALIFICATIONS AND ROLE OF THE POTENTIAL WINNER IN THE CREATION OF THE SUBMISSION. No Submission or Entrant is a winner until post-competition prize affidavits are completed and verified, even if announced verbally or on the website. Final decision to designate a winner is made by Sponsor and/or Administrator.

**Prize Delivery:** Payable to the individual Entrant, the Team's Representative, or the Organization. The Representative allocates a Team/Organization prize. A monetary Prize is mailed to the winning Entrant's/Representative's address or sent electronically to their bank account, only after receipt of the completed winner affidavit and other Required Forms. Deadline to return Required Forms: ten (10) business days after they are sent. Incorrect info may cause delayed delivery, disqualification, or forfeiture. Prizes delivered within 60 days of Sponsor/Devpost receiving completed Required Forms.

**Fees & Taxes:** Winners (and all participating Team/Organization members) are responsible for any fees (wiring, currency exchange) and for reporting/paying all applicable taxes (federal, state/provincial/territorial, local). May need to submit tax/other forms for withholding/reporting compliance (US residents: W-9; other countries: W-8BEN). Winners must comply with foreign exchange and banking regulations and report receipt of the Prize to relevant agencies if necessary. Sponsor, Devpost, and/or Prize provider may withhold a portion to comply with US or other applicable tax laws.

## 9. Entry Conditions and Release

By entering, you (and each participating Team/Organization member) agree that:
- The relationship between you and the Sponsor/Administrator is not a confidential, fiduciary, or other special relationship.
- You will be bound by and comply with these Official Rules and the decisions of the Sponsor, Administrator, and/or Judges, which are binding and final.
- You release, indemnify, defend, and hold harmless the Promotion Entities and their respective parent/subsidiary/affiliated companies, Prize suppliers, and any other organizations responsible for sponsoring/fulfilling/administering/advertising/promoting the Hackathon, and all their past and present officers, directors, employees, agents, and representatives (the "Released Parties") from any and all claims, expenses, and liabilities (including reasonable attorneys' fees) - including negligence and damages of any kind to persons and property, defamation, slander, libel, violation of right of publicity, infringement of trademark/copyright/other IP rights, property damage, death or personal injury - arising out of or relating to entry, creation/entry of a Submission, participation, acceptance/use/misuse of the Prize (including any related travel/activity), and/or the broadcast/transmission/performance/exploitation/use of the Submission as authorized by these Rules.

The Released Parties have no liability for:
- Any incorrect or inaccurate information (whether caused by Sponsor/Administrator electronic or printing error or by equipment/programming used in the Hackathon).
- Technical failures of any kind (malfunctions, interruptions, or disconnections in phone lines, internet connectivity, electronic transmission errors, network hardware/software, or failure of the Hackathon Website).
- Unauthorized human intervention in any part of the entry process or Hackathon.
- Technical or human error in administration or processing of Submissions.
- Any injury or damage to persons/property caused directly or indirectly from participation or receipt/use/misuse of any Prize.

The Released Parties are not responsible for incomplete, late, misdirected, damaged, lost, illegible, or incomprehensible Submissions or for address/email changes. Proof of sending/submitting is not proof of receipt. If a Submission is determined not received or erroneously deleted/lost/destroyed/corrupted, the Entrant's sole remedy is to request the opportunity to resubmit - requested promptly after the Entrant knows or should have known of a problem, and determined at Sponsor's sole discretion.

## 10. Publicity

By participating, Entrant consents to the promotion and display of the Submission and to the use of personal information for promotional purposes by Sponsor, Administrator, and third parties acting on their behalf. Such personal information includes but is not limited to name, likeness, photograph, voice, opinions, comments, hometown, and country of residence. It may be used in any existing or newly created media, worldwide, without further payment/consideration/right of review, unless prohibited by law. Authorized use includes advertising and promotional purposes.

## 11. General Conditions

- Sponsor and Administrator may cancel, suspend, and/or modify the Hackathon (or any part) in the event of technical failure, fraud, or any other unanticipated/uncontrollable factor.
- Sponsor and Administrator may disqualify any individual/Entrant found to be (or appearing to be) tampering with the entry process or operation, or acting in violation of these Rules or in a manner that is inappropriate, unsportsmanlike, not in the best interests of the Hackathon, or in violation of any law/regulation.
- Any attempt to undermine the Hackathon may violate criminal and civil law; Sponsor/Administrator may take appropriate action, including requiring cooperation with an investigation and referral to law enforcement.
- If there is any discrepancy/inconsistency between the Official Rules and other Hackathon materials (including the submission form, website, or advertising), the Official Rules prevail.
- The Official Rules are subject to change at any time. Sponsor and Administrator will post amended Official Rules on the Hackathon Website; amendments become effective at the time specified in the posting or, if none, at the time of posting.
- If, before the deadline, an Entrant or prospective Entrant believes any term is or may be ambiguous, they must submit a written request for clarification.
- Failure to enforce any term is not a waiver. If any provision becomes illegal/unenforceable in a jurisdiction, the remainder stays valid; the illegal/unenforceable provision is replaced by a valid, enforceable one closest to the Sponsor's intention.
- Excluding Submissions, all IP related to the Hackathon (copyrighted material, trademarks, trade-names, logos, designs, promotional materials, web pages, source codes, drawings, illustrations, slogans, representations) is owned or used under license by Sponsor and/or Administrator. All rights reserved. Unauthorized copying/use of any copyrighted material or IP without express written consent is prohibited. Any use of Sponsor/Administrator IP in a Submission is solely to the extent provided in these Rules.

## 12. Limitations of Liability

By entering, all Entrants (including all participating Team/Organization members) agree to release the Released Parties from all liability in connection with the Prizes or participation. However, any liability limitation regarding gross negligence or intentional acts, or events of death or bodily injury, does not apply in jurisdictions where such limitation is not legal.

## 13. Disputes

Except where prohibited by law, as a condition of participating, Entrant agrees that:
- Any and all disputes and causes of action arising out of or connected with the Hackathon or any Prizes shall be resolved individually, without any class action, and exclusively by final and binding arbitration under the rules of the American Arbitration Association, held at the AAA regional office nearest the contestant.
- The Federal Arbitration Act governs interpretation, enforcement, and all proceedings at such arbitration.
- Judgment upon the arbitration award may be entered in any court having jurisdiction.

Under no circumstances may an Entrant obtain (and Entrant waives all rights to claim) punitive, incidental, or consequential damages, or any other damages including attorneys' fees, other than actual out-of-pocket expenses (i.e., costs of entering). Entrant waives all rights to have damages multiplied or increased. All issues concerning construction, validity, interpretation, and enforceability of these Rules, or the rights/obligations of Entrant and Sponsor, are governed by the substantive laws of the State of New York, USA, without regard to New York choice-of-law rules.

> SOME JURISDICTIONS DO NOT ALLOW THE LIMITATIONS OR EXCLUSION OF LIABILITY FOR INCIDENTAL OR CONSEQUENTIAL DAMAGES, SO THE ABOVE LIMITATIONS OF LIABILITY MAY NOT APPLY TO YOU.

## 14. Additional Terms

Review the Devpost Terms of Service at https://info.devpost.com/terms for additional rules that apply. Those Terms of Service are incorporated by reference into these Official Rules (the term "Poster" in the Terms of Service means "Sponsor" here). If there is a conflict between the Terms of Service and these Official Rules, these Official Rules control with respect to this Hackathon only.

## 15. Entrant's Personal Information

Information collected from Entrants is subject to Devpost's Privacy Policy at https://info.devpost.com/privacy.

**For questions, email support@devpost.com.**
