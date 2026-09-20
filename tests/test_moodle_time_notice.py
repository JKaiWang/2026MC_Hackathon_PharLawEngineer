from commute_agent.tools.course_mail import parse_moodle_time_change

SUBJECT = '1151_影像處理、電腦視覺及...(1151_F743000)： 下周二(9/22)上課時間改為早上10:10'
BODY = '由 侯冠翔發表於2026年 09月 15日(Tue) 18:04\n下周二(9/22)上課時間改為早上10:10'


def test_real_notice_is_one_dated_start_change():
    result = parse_moodle_time_change(SUBJECT, BODY)
    assert result['effective_date'] == '2026-09-22'
    assert result['new_time'] == '10:10'
    assert result['course_code'] == '1151_F743000'
    assert result['scope'] == 'single_occurrence'
    assert result['new_location'] is None
    assert 'end_time' not in result


def test_missing_year_or_conflicting_times_need_review():
    assert parse_moodle_time_change(SUBJECT, '') is None
    assert parse_moodle_time_change(SUBJECT, BODY + '\n(9/22)上課時間改為下午2:00') is None


def test_invalid_date_is_not_accepted():
    assert parse_moodle_time_change(SUBJECT.replace('9/22', '2/30'), BODY.replace('9/22', '2/30')) is None
