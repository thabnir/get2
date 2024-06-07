import configparser
import requests
from requests.compat import urljoin
import logging
import uuid
import sys
import datetime
from bs4 import BeautifulSoup


def follow_refresh(session, request_func):
    response = request_func()
    if response.status_code != 200:
        logging.error(f"Failed to get {response.url} with status code {response.status_code}")
        exit(1)

    # Check for redirect via meta tag refresh msg. Follow the redirect.
    soup = BeautifulSoup(response.text, "html.parser")
    meta_refresh = soup.find("meta", attrs={"http-equiv": "refresh"})
    if meta_refresh:
        content = meta_refresh.get("content")
        if content:
            wait_time, new_url = content.split(";url=")
            new_url = urljoin(response.url, new_url)
            logging.info(f"Waiting for {wait_time} seconds before redirecting to {new_url}")
            final_response = session.get(new_url)
            if final_response.status_code != 200:
                logging.error(f"Failed to get {new_url} with status code {final_response.status_code}")
                exit(1)
            return final_response

    return response


def get_courses_to_waitlist(soup):
    table = soup.find(
        "table", {"class": "datadisplaytable", "summary": "This layout table is used to present Registration Errors."}
    )
    rows = table.find_all("tr")
    courses_to_waitlist = []
    for row in rows[1:]:  # Skip header row
        cells = row.find_all("td")
        if cells and len(cells) >= 3:
            action_cell = cells[1]
            crn = cells[2].text.strip()
            select_element = action_cell.find("select")
            if select_element:
                option = select_element.find("option", value="LW")
                if option:
                    courses_to_waitlist.append(
                        {
                            "assoc_term_in": row.find("input", {"name": "assoc_term_in"})["value"],
                            "CRN_IN": crn,
                            "RSTS_IN": "LW",  # Add to waitlist
                            "start_date_in": row.find("input", {"name": "start_date_in"})["value"],
                            "end_date_in": row.find("input", {"name": "end_date_in"})["value"],
                            "SUBJ": row.find("input", {"name": "SUBJ"})["value"],
                            "CRSE": row.find("input", {"name": "CRSE"})["value"],
                            "SEC": row.find("input", {"name": "SEC"})["value"],
                            "LEVL": row.find("input", {"name": "LEVL"})["value"],
                            "CRED": row.find("input", {"name": "CRED"})["value"],
                            "GMOD": row.find("input", {"name": "GMOD"})["value"],
                            "TITLE": row.find("input", {"name": "TITLE"})["value"],
                        }
                    )
    return courses_to_waitlist


logging.basicConfig(
    filename="get2.log",
    filemode="a",
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger().addHandler(logging.StreamHandler(sys.stdout))
logging.info("get2.py started")

config = configparser.ConfigParser(allow_no_value=True)  # allow comments in the config files
config.read("config.ini")
login_data = {
    "sid": config["secrets"]["student_id"],
    "PIN": config["secrets"]["minerva_pin"],
}

if not login_data or not login_data["sid"] or not login_data["PIN"]:
    logging.error("Config file read failed. Exiting.")
    exit(1)

config.clear()
config.read("courses.ini")
course_dict = {}
for semester in config.sections():
    crns = config[semester]["crns"].split(" ")
    crns = [crn for crn in crns if crn]
    course_dict[semester] = crns
course_dict = {semester: crns for semester, crns in course_dict.items() if crns}

logging.info(f"Listing SEMESTER:CRN pairs: {course_dict}")

if not course_dict:
    logging.error("Course file read failed. Exiting.")
    exit(1)

timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")

# Done reading config files, time to start the requests

domain = "https://horizon.mcgill.ca"
login_url = f"{domain}/pban1/twbkwbis.P_ValLogin"
registration_url = f"{domain}/pban1/bwckcoms.P_Regs"

with requests.Session() as session:
    logging.info("Attempting to log in...")
    res = session.get(login_url)
    cookies = dict(res.cookies)
    response = follow_refresh(session, lambda: session.post(login_url, data=login_data, cookies=cookies))
    attempt_id = uuid.uuid1()

    if response.status_code != 200:
        logging.error(f"Failed to log in with status code {response.status_code}")
        exit(1)

    logging.info("Logged in successfully.")
    # save response to html file with unique name
    with open(f"logins/login_{timestamp}_{attempt_id}.html", "w") as f:
        f.write(response.text)
        logging.info(f"Saved login response to file {f.name}")

    # register for each course one semester at a time
    for semester, crns in course_dict.items():
        # makes independent queries for each semester since that's how the form works
        base_query_url = f"{registration_url}?term_in={semester}&RSTS_IN=DUMMY&assoc_term_in=DUMMY&CRN_IN=DUMMY&start_date_in=DUMMY&end_date_in=DUMMY&SUBJ=DUMMY&CRSE=DUMMY&SEC=DUMMY&LEVL=DUMMY&CRED=DUMMY&GMOD=DUMMY&TITLE=DUMMY&MESG=DUMMY&REG_BTN=DUMMY"
        registration_url = base_query_url
        for crn in crns:
            registration_url += f"&RSTS_IN=RW&CRN_IN={crn}&assoc_term_in=&start_date_in=&end_date_in="
        registration_url += "&regs_row=0&wait_row=0&add_row=10&REG_BTN=Submit+Changes"

        logging.info(f"Attempting to register with {registration_url}")
        response = follow_refresh(session, lambda query_url=registration_url: session.get(query_url))

        with open(f"registrations/register_{timestamp}_{attempt_id}_{semester}.html", "w") as f:
            f.write(response.text)
            logging.info(f"Saved registration attempt response to file {f.name}")

        if response.status_code != 200:
            logging.error(f"Registration request failed with status code {response.status_code}")
            exit(1)
        logging.info(f"Registered for {semester}: {crns}")

        # add any courses to waitlist if available
        soup = BeautifulSoup(response.text, "html.parser")
        courses_to_waitlist = get_courses_to_waitlist(soup)
        if len(courses_to_waitlist) == 0:
            logging.info(f"No courses to waitlist for {semester}")
        else:
            logging.info(f"Found {len(courses_to_waitlist)} courses to waitlist for {semester}")
            for course in courses_to_waitlist:
                logging.info(f"Waitlistable course: \"{course['TITLE']}\" with CRN: {course['CRN_IN']}")

        waitlist = "LW"  # add to waitlist. "RW" is to register normally
        waitlist_url = base_query_url
        for course in courses_to_waitlist:
            waitlist_url += f"&RSTS_IN={waitlist}&CRN_IN={course['CRN_IN']}&assoc_term_in=&start_date_in=&end_date_in="

        waitlist_url += "&regs_row=0&wait_row=0&add_row=10&REG_BTN=Submit+Changes"
        response = follow_refresh(session, lambda course=course, url=waitlist_url: session.post(url, data=course))
        # save response to html file with unique name
        with open(f"waitlists/waitlist_{timestamp}_{attempt_id}_{semester}_{course['CRN_IN']}.html", "w") as f:
            f.write(response.text)
            logging.info(f"Saved waitlist attempt response to file {f.name}")
        if response.status_code != 200:
            logging.error(
                f"Request to join waitlist for {course['CRN_IN']} failed with status code {response.status_code}"
            )
            exit(1)
        logging.info(f"Joined waitlist for {course['TITLE']} with CRN: {course['CRN_IN']} in semester {semester}")


logging.info("get2.py finished")
