You are role-playing a client on a freelance marketplace who posted the job below and is
reading one proposal. Judge it the way this specific client would, using their private
preferences. Reply with JSON only: {{"score": <integer 0-10>, "reason": "<one sentence>"}}.

Score guide: 0-3 you would ignore it; 4-5 you would read it but not reply; 6-7 you would
reply with a question; 8-10 you would invite the freelancer to an interview.

Job title: {title}
Job description:
{description}

Your private preferences (the freelancer cannot see these):
- Must-have skills the proposal has to show real evidence for: {must_have}
- You dislike generic openings ("Dear Hiring Manager", "I am the perfect fit"): {dislikes_generic}
- Preferred proposal length: {preferred_length}

The freelancer bid: ${bid}

Proposal:
{cover_letter}

Screening answers:
{answers}
