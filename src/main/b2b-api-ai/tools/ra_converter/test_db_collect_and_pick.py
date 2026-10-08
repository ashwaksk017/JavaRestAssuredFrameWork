"""A script that reads a list from the database and picks one entry must
read the database, run BEFORE the script that uses the pick, and hand the
pick on.

leadspace_attestation_suite, B2B-8883_H4B_manged_emaildomain:

    GroovyScript_accountRules   SELECT value FROM segment.account_rules
                                WHERE reason = 'managed_domain'
                                -> a random row -> Properties.managed_email_domain
    DataGenInput                def managedDomain = context.expand(
                                    '${Properties#managed_email_domain}')
                                Domain / Email / website built on it
    createAccount               emailDomains: ["${Properties#managed_email_domain}"]

Three things went wrong, each enough on its own:

1. The read was a comment. The query is a triple-quoted string and the
   closure appends to a list; the eachRow recogniser knows a single-line
   query and `outer = row.field`. The property kept the value saved in the
   datasheet by somebody's last ReadyAPI run.
2. The audit said FULL, because the triple-quoted string had been
   recognised.
3. DataGenInput ran FIRST. A DB-only script gets a phase of its own, and
   every setup step is gathered into the bootstrap wherever it sat.

The request went out with last run's domain in emailDomains and a random
domain on the owner: 400, "Email address domain must match an allowed
domain within program account".

    python tools/ra_converter/test_db_collect_and_pick.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import fluent_scenario as fs  # noqa: E402
import groovy_translator as gt  # noqa: E402

Q3 = '"' * 3

ACCOUNT_RULES = '''
import groovy.sql.Sql
def dbUrl = context.expand('${#Project#DB_URL}')
def sql = Sql.newInstance(dbUrl, dbUser, dbPassword, dbDriver)
// --- Query for all managed email domains ---
def query = ''' + Q3 + '''
    SELECT value
    FROM segment.account_rules
    WHERE reason = 'managed_domain';
''' + Q3 + '''
def domains = []
sql.eachRow(query) { row ->
    if (row.value) {
        domains.add(row.value.toString().trim())
    }
}
sql.close()
if (domains.isEmpty()) {
    throw new Exception("No managed domains found in segment.account_rules")
}
def randomDomain = domains[new Random().nextInt(domains.size())]
def PropertiesPropertyVal2 = testRunner.testCase.getTestStepByName("Properties")
PropertiesPropertyVal2.setPropertyValue("managed_email_domain", randomDomain)
log.info "Random domain selected: ${randomDomain}"
'''

FREE_DOMAIN = ACCOUNT_RULES.replace(
    'setPropertyValue("managed_email_domain", randomDomain)',
    'setPropertyValue("free_email_domain", randomDomain+".com")')

DATAGEN = '''
def generator = { String alphabet, int n -> "x" }
def PropertiesPropertyVal = testRunner.testCase.getTestStepByName("Properties")
def generatedUser = generator( (('a'..'z')+('0'..'9')).join(), 5 )
def generatedUser1 = generator( (('a'..'z')+('a'..'z')).join(), 6)
def managedDomain = context.expand('${Properties#managed_email_domain}')
def generatedDomain =  managedDomain
def generatedEmail = generatedUser + "@" + generatedDomain
def generatedemailAddress = generatedUser1 + "@"+managedDomain
PropertiesPropertyVal.setPropertyValue("Domain", generatedDomain)
PropertiesPropertyVal.setPropertyValue("Email", generatedEmail)
PropertiesPropertyVal.setPropertyValue("generatedemailAddress", generatedemailAddress)
'''


def _java(script, hint="s"):
    lines, meta = gt.translate(script, {}, hint)
    return "\n".join(lines), meta


# --- 1. the read ---------------------------------------------------------

def test_the_block_is_recognised_with_its_query_column_and_pick():
    assert gt.collect_and_pick_blocks(ACCOUNT_RULES) == [{
        "query_var": "query",
        "sql": "SELECT value FROM segment.account_rules WHERE reason = 'managed_domain'",
        "column": "value", "list_var": "domains", "pick_var": "randomDomain"}]


def test_the_database_is_read_and_the_pick_is_published():
    java, meta = _java(ACCOUNT_RULES, "GroovyScript_accountRules")
    assert "cannot safely inline" not in java, "the read is still a comment:\n" + java
    assert ('String __jdbcSql = "SELECT value FROM segment.account_rules '
            "WHERE reason = 'managed_domain'\";") in java, java
    assert "Db.queryAll(__jdbcSql)" in java
    assert '__row.get("value")' in java
    assert "__picked.get(new java.util.Random().nextInt(__picked.size()))" in java
    assert 'TestSupport.putExtracted(ctx, "Properties.managed_email_domain", randomDomain);' in java
    assert meta["coverage"] == "FULL"
    assert "jdbc_eachRow_collect_pick" in meta["patterns_matched"]


def test_an_empty_pick_publishes_nothing_and_says_so():
    """ReadyAPI throws when the table has no such rows. Publishing "" would
    blank the property; the value must only be written when one was read."""
    java, _ = _java(ACCOUNT_RULES)
    publish = 'putExtracted(ctx, "Properties.managed_email_domain", randomDomain)'
    guard = "if (randomDomain.isEmpty()) {"
    assert guard in java and java.index(guard) < java.index(publish)
    assert "the ReadyAPI script fails here" in java


def test_a_pick_published_with_a_suffix_keeps_the_suffix():
    java, _ = _java(FREE_DOMAIN)
    assert ('TestSupport.putExtracted(ctx, "Properties.free_email_domain", '
            'randomDomain + ".com");') in java, java
    assert gt.collect_and_pick_fields(FREE_DOMAIN) == [("Properties", "free_email_domain")]


def test_the_query_goes_out_without_its_trailing_semicolon_or_layout():
    sql = gt.collect_and_pick_blocks(ACCOUNT_RULES)[0]["sql"]
    assert not sql.endswith(";") and "\n" not in sql and "  " not in sql, repr(sql)


# --- what it must NOT claim ----------------------------------------------

def test_a_query_with_an_interpolated_value_is_not_a_constant():
    s = ACCOUNT_RULES.replace("WHERE reason = 'managed_domain'",
                              "WHERE reason = '${reason}'")
    assert gt.collect_and_pick_blocks(s) == []


def test_a_closure_that_reads_two_columns_is_not_guessed():
    s = ACCOUNT_RULES.replace("domains.add(row.value.toString().trim())",
                              "domains.add(row.value.toString() + row.reason)")
    assert gt.collect_and_pick_blocks(s) == []


def test_a_list_nobody_picks_from_is_left_alone():
    s = ACCOUNT_RULES.replace(
        "def randomDomain = domains[new Random().nextInt(domains.size())]",
        "def randomDomain = domains[0]")
    assert gt.collect_and_pick_blocks(s) == []


def test_a_commented_out_loop_is_not_a_loop():
    s = "\n".join("// " + l if "sql.eachRow" in l else l
                  for l in ACCOUNT_RULES.splitlines())
    assert gt.collect_and_pick_blocks(s) == []


def test_the_ordinary_eachRow_shape_is_untouched():
    otp = ('def id = context.expand(\'${a#Response#$.memberId}\')\n'
           'def q = "select email_otp from t where id = " + id\n'
           "def emailOtp = ''\n"
           "sql.eachRow(q) { row ->\nemailOtp = row.email_otp.toString().trim()\n}\n")
    assert gt.collect_and_pick_blocks(otp) == []
    java, _ = _java(otp)
    assert "Db.pollUntilStable" in java or "Db.queryAll" in java, java
    assert "collect `" not in java


# --- 2. the audit --------------------------------------------------------

def test_a_read_that_is_still_dropped_is_not_reported_full():
    """Nothing in the 29 suites hits this after the fix above -- which is
    exactly when a silent FULL would come back unnoticed."""
    s = ('def q = buildQuery(a, b)\n'
         "def x = ''\n"
         "sql.eachRow(q) { row ->\nx = row.v\n}\n"
         'def p = testRunner.testCase.getTestStepByName("Properties")\n'
         'p.setPropertyValue("k", "literal")\n')
    java, meta = _java(s)
    assert "cannot safely inline" in java
    assert meta["coverage"] == "PARTIAL", meta


# --- 3. the hand-off into the next script --------------------------------

def test_a_domain_read_from_an_earlier_steps_property_is_published_and_pinned():
    assert gt._ctx_sourced_vars(DATAGEN) == {
        "managedDomain": 'TestSupport.ctxGet(ctx, "Properties.managed_email_domain")',
        "generatedDomain": 'TestSupport.ctxGet(ctx, "Properties.managed_email_domain")'}
    java, _ = _java(DATAGEN, "DataGenInput")
    read = 'String __earlier = TestSupport.ctxGet(ctx, "Properties.managed_email_domain");'
    guard = "if (__earlier != null && !__earlier.trim().isEmpty()) {"
    publish = 'TestSupport.putExtracted(ctx, "Properties.Domain", __earlier.trim());'
    # Database not read -> the earlier step published nothing -> write
    # nothing, so the Properties step still seeds the saved value.
    assert read in java and guard in java and publish in java, java
    assert java.index(read) < java.index(guard) < java.index(publish)
    assert "ImportedScenario.pinAuthorDomain(ctx);" in java
    assert java.index(publish) < java.index("ImportedScenario.pinAuthorDomain(ctx);")


def test_only_the_domain_is_taken_from_ctx_not_every_expanded_property():
    """The emails and usernames a script derives are per-run data; freezing
    them to whatever an earlier step left would re-send last run's."""
    s = ("def prev = context.expand('${Properties#Email}')\n"
         'def p = testRunner.testCase.getTestStepByName("Other")\n'
         'p.setPropertyValue("copyOfEmail", prev)\n')
    pubs = gt._var_backed_publications(s)
    assert ("Other", "copyOfEmail") not in pubs, pubs


def test_a_property_the_script_writes_itself_is_not_an_earlier_steps():
    s = ('def p = testRunner.testCase.getTestStepByName("Properties")\n'
         'p.setPropertyValue("managed_email_domain", "x.com")\n'
         "def d = context.expand('${Properties#managed_email_domain}')\n"
         'p.setPropertyValue("Domain", d)\n')
    assert gt._ctx_sourced_vars(s) == {}


def test_a_project_property_is_not_a_step_property():
    s = "def d = context.expand('${#Project#ALLOWED_DOMAINS}')\n"
    assert gt._ctx_sourced_vars(s) == {}


# --- 4. the order --------------------------------------------------------

class GroovyStep:
    def __init__(self, name, script):
        self.step_name, self.script = name, script


class RestStep:
    http_method, assertions = "POST", []

    def __init__(self, name, path):
        self.step_name, self.resource_path = name, path


def _starts(steps):
    banner = {"DataGenInput": "identities", "enroll": "enroll guest"}
    start, _flow, _verify = fs.group_flow_and_verify(
        steps, lambda s: banner.get(s.step_name))
    return [s.step_name for s in start]


def test_the_pick_stays_ahead_of_the_script_that_reads_it():
    steps = [GroovyStep("GroovyScript_accountRules", ACCOUNT_RULES),
             GroovyStep("DataGenInput", DATAGEN),
             RestStep("enroll", "/realms/guests/enroll")]
    assert fs.db_phase_name(ACCOUNT_RULES), "precondition: this script IS given its own name"
    assert _starts(steps) == ["GroovyScript_accountRules", "DataGenInput"], _starts(steps)


def test_a_db_script_nothing_in_the_setup_reads_keeps_its_own_phase():
    """NEGATIVE CONTROL: 120 of the 138 cases with a DB script ahead of
    more setup are cleanups and updates. They must not move."""
    cleanup = ('def sql = Sql.newInstance(a, b, c, d)\n'
               'sql.execute("DELETE FROM account WHERE web_site = \'x\'")\n')
    steps = [GroovyStep("CleanupDB", cleanup),
             GroovyStep("DataGenInput", DATAGEN),
             RestStep("enroll", "/realms/guests/enroll")]
    assert _starts(steps) == ["DataGenInput"], _starts(steps)


def test_a_pick_no_later_setup_step_reads_keeps_its_own_phase():
    other = DATAGEN.replace("managed_email_domain", "something_else")
    steps = [GroovyStep("GroovyScript_accountRules", ACCOUNT_RULES),
             GroovyStep("DataGenInput", other),
             RestStep("enroll", "/realms/guests/enroll")]
    assert _starts(steps) == ["DataGenInput"], _starts(steps)


if __name__ == "__main__":
    passed = 0
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok  " + name)
            passed += 1
    print("%d passed" % passed)
