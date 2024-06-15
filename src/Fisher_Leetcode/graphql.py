from graphql_query import Argument, Field, Operation, Query, Variable


def get_question_graphql_query() -> str:
    titleSlug = Variable(name="titleSlug", type="String!")
    query = Query(
        name="question",
        arguments=[Argument(name="titleSlug", value=titleSlug)],
        fields=[
            Field(name="questionId"),
            Field(name="questionFrontendId"),
            Field(name="title"),
            Field(name="titleSlug"),
            Field(name="acRate"),
            Field(name="difficulty"),
            Field(name="freqBar"),
            Field(name="likes"),
            Field(name="dislikes"),
            Field(name="content"),
            Field(name="similarQuestions"),
            Field(name="isFavor"),
            Field(name="isPaidOnly"),
            Field(name="status"),
            Field(name="hasVideoSolution"),
            Field(name="hasSolution"),
            Field(
                name="topicTags",
                fields=[
                    Field(name="name"),
                    Field(name="id"),
                    Field(name="slug"),
                ],
            ),
        ],
    )
    operation = Operation(
        type="query", name="questionData", variables=[titleSlug], queries=[query]
    )
    return operation.render()


def get_daily_challenge_graphql_query() -> str:
    question = Field(
        name="question",
        fields=[
            Field(name="questionId"),
            Field(name="questionFrontendId"),
            Field(name="title"),
            Field(name="titleSlug"),
            Field(name="acRate"),
            Field(name="difficulty"),
            Field(name="freqBar"),
            Field(name="likes"),
            Field(name="dislikes"),
            Field(name="content"),
            Field(name="similarQuestions"),
            Field(name="isFavor"),
            Field(name="isPaidOnly"),
            Field(name="status"),
            Field(name="hasVideoSolution"),
            Field(name="hasSolution"),
            Field(
                name="topicTags",
                fields=[
                    Field(name="name"),
                    Field(name="id"),
                    Field(name="slug"),
                ],
            ),
        ],
    )
    query = Query(
        name="activeDailyCodingChallengeQuestion",
        fields=[
            Field(name="date"),
            Field(name="userStatus"),
            Field(name="link"),
            question,
        ],
    )
    operation = Operation(type="query", name="questionOfToday", queries=[query])
    return operation.render()


def get_submission_graphql_query() -> str:
    submission_int_id = Variable(name="submissionIntId", type="Int!")
    submission_id = Variable(name="submissionId", type="ID!")
    user = Field(
        name="user",
        fields=[
            Field(name="username"),
            Field(
                name="profile",
                fields=[Field(name="realName"), Field(name="userAvatar")],
            ),
        ],
    )
    lang = Field(name="lang", fields=[Field(name="name"), Field(name="verboseName")])
    question = Field(
        name="question",
        fields=[
            Field(name="questionFrontendId"),
            Field(name="title"),
            Field(name="titleSlug"),
            Field(name="difficulty"),
            Field(name="isPaidOnly"),
        ],
    )
    topic_tags = Field(
        name="topicTags",
        fields=[Field(name="tagId"), Field(name="slug"), Field(name="name")],
    )
    submission_query = Query(
        name="submissionDetails",
        arguments=[Argument(name="submissionId", value=submission_int_id)],
        fields=[
            Field(name="runtime"),
            Field(name="runtimeDisplay"),
            Field(name="runtimePercentile"),
            Field(name="memory"),
            Field(name="memoryDisplay"),
            Field(name="memoryPercentile"),
            Field(name="memoryDistribution"),
            Field(name="code"),
            Field(name="timestamp"),
            Field(name="statusCode"),
            user,
            lang,
            question,
            Field(name="notes"),
            topic_tags,
            Field(name="runtimeError"),
            Field(name="compileError"),
            Field(name="lastTestcase"),
        ],
    )
    complexity_query = Query(
        name="submissionComplexity",
        arguments=[Argument(name="submissionId", value=submission_id)],
        fields=[
            Field(
                name="timeComplexity",
                fields=[
                    Field(name="complexity"),
                    Field(name="displayName"),
                    Field(name="funcStr"),
                    Field(name="vote"),
                ],
            ),
            Field(
                name="memoryComplexity",
                fields=[
                    Field(name="complexity"),
                    Field(name="displayName"),
                    Field(name="funcStr"),
                    Field(name="vote"),
                ],
            ),
            Field(name="isLimited"),
        ],
    )
    operation = Operation(
        type="query",
        name="submissionDetails",
        variables=[submission_int_id, submission_id],
        queries=[submission_query, complexity_query],
    )
    return operation.render()


def get_today_question_graphql_query() -> str:
    query = Query(
        name="activeDailyCodingChallengeQuestion",
        fields=[
            Field(name="date"),
            Field(name="userStatus"),
            Field(name="link"),
            Field(
                name="question",
                fields=[
                    Field(name="questionFrontendId"),
                    Field(name="title"),
                    Field(name="titleSlug"),
                    Field(name="difficulty"),
                    Field(name="freqBar"),
                    Field(name="likes"),
                    Field(name="dislikes"),
                    Field(name="content"),
                    Field(name="similarQuestions"),
                    Field(name="isFavor"),
                    Field(name="isPaidOnly"),
                    Field(name="status"),
                    Field(name="hasVideoSolution"),
                    Field(name="hasSolution"),
                    Field(
                        name="topicTags",
                        fields=[
                            Field(name="name"),
                            Field(name="id"),
                            Field(name="slug"),
                        ],
                    ),
                ],
            ),
        ],
    )
    operation = Operation(type="query", name="questionOfToday", queries=[query])
    return operation.render()
