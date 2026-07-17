// =============================================================================
// Post -- POJO matching the jsonplaceholder /posts payload
// -----------------------------------------------------------------------------
// Concrete example of typed response deserialization -- use RestUtilities.as()
// to bind, then assert on typed fields instead of substring-matching JSON.
// =============================================================================

package com.ak.api.models;

import com.fasterxml.jackson.annotation.JsonInclude;
import com.fasterxml.jackson.annotation.JsonInclude.Include;
import com.fasterxml.jackson.annotation.JsonProperty;

@JsonInclude(Include.NON_NULL)
public class Post {

    @JsonProperty("id")
    private Integer id;

    @JsonProperty("userId")
    private Integer userId;

    @JsonProperty("title")
    private String title;

    @JsonProperty("body")
    private String body;

    public Post() {
    }

    public Post(Integer id, Integer userId, String title, String body) {
        this.id = id;
        this.userId = userId;
        this.title = title;
        this.body = body;
    }

    public Integer getId()               { return id; }
    public void setId(Integer id)        { this.id = id; }

    public Integer getUserId()           { return userId; }
    public void setUserId(Integer u)     { this.userId = u; }

    public String getTitle()             { return title; }
    public void setTitle(String t)       { this.title = t; }

    public String getBody()              { return body; }
    public void setBody(String b)        { this.body = b; }

    @Override
    public String toString() {
        return "Post{id=" + id + ", userId=" + userId
                + ", title='" + title + "', body='" + body + "'}";
    }
}
