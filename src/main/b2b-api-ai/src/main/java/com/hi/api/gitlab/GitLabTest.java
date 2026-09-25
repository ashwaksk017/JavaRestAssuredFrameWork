package com.hi.api.gitlab;

import java.lang.annotation.ElementType;
import java.lang.annotation.Retention;
import java.lang.annotation.RetentionPolicy;
import java.lang.annotation.Target;

/**
 * GitLab issue / test-case IID to sync (Quality test case or plain issue).
 * CSV column {@code gitlab_test_case_id} wins when both are present.
 */
@Retention(RetentionPolicy.RUNTIME)
@Target(ElementType.METHOD)
public @interface GitLabTest {
    /** Project issue IID, e.g. {@code "42"}. */
    String value();
}
