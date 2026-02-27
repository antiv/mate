import re
import json
from typing import Dict, Any, List, Optional
from google.adk.tools.tool_context import ToolContext

class CVAnalyzer:
    """Analyzes CV content extracted from Google Drive documents."""
    
    def __init__(self):
        self.email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        self.phone_pattern = r'(\+?[\d\s\-\(\)]{10,})'
        self.linkedin_pattern = r'linkedin\.com/in/[\w\-]+'
        
    def extract_contact_info(self, content: str) -> Dict[str, Any]:
        """Extract contact information from CV content."""
        emails = re.findall(self.email_pattern, content)
        phones = re.findall(self.phone_pattern, content)
        linkedin = re.findall(self.linkedin_pattern, content)
        
        return {
            "emails": list(set(emails)),
            "phones": list(set(phones)),
            "linkedin": list(set(linkedin))
        }
    
    def extract_skills(self, content: str) -> List[str]:
        """Extract skills from CV content."""
        # Common skill keywords
        skill_keywords = [
            "Python", "Java", "JavaScript", "React", "Node.js", "Angular", "Vue.js",
            "SQL", "MongoDB", "PostgreSQL", "MySQL", "AWS", "Azure", "GCP",
            "Docker", "Kubernetes", "Git", "Jenkins", "CI/CD", "REST API",
            "GraphQL", "Microservices", "Machine Learning", "AI", "Data Science",
            "HTML", "CSS", "TypeScript", "PHP", "C++", "C#", ".NET",
            "Spring", "Django", "Flask", "Express.js", "Laravel", "WordPress",
            "Agile", "Scrum", "Kanban", "JIRA", "Confluence", "Figma",
            "Photoshop", "Illustrator", "Adobe Creative Suite", "Excel", "PowerPoint",
            "Salesforce", "HubSpot", "Marketing", "SEO", "SEM", "Google Analytics"
        ]
        
        found_skills = []
        content_lower = content.lower()
        
        for skill in skill_keywords:
            if skill.lower() in content_lower:
                found_skills.append(skill)
        
        return found_skills
    
    def extract_experience(self, content: str) -> List[Dict[str, Any]]:
        """Extract work experience from CV content."""
        # Look for common experience patterns
        experience_patterns = [
            r'(\d{4})\s*[-–]\s*(\d{4}|Present|Current)',
            r'(\w+\s+\d{4})\s*[-–]\s*(\w+\s+\d{4}|Present|Current)',
            r'(\d{4})\s*[-–]\s*(\d{4}|Present|Current)',
        ]
        
        experiences = []
        lines = content.split('\n')
        
        for i, line in enumerate(lines):
            for pattern in experience_patterns:
                matches = re.findall(pattern, line)
                if matches:
                    # Try to extract company and position from surrounding lines
                    company = ""
                    position = ""
                    
                    # Look for company/position in nearby lines
                    for j in range(max(0, i-3), min(len(lines), i+4)):
                        if j != i:
                            nearby_line = lines[j].strip()
                            if nearby_line and len(nearby_line) > 3:
                                if not company and len(nearby_line) < 100:
                                    company = nearby_line
                                elif not position and len(nearby_line) < 100:
                                    position = nearby_line
                    
                    experiences.append({
                        "duration": line.strip(),
                        "company": company,
                        "position": position,
                        "raw_line": line.strip()
                    })
                    break
        
        return experiences
    
    def extract_education(self, content: str) -> List[Dict[str, Any]]:
        """Extract education information from CV content."""
        education_keywords = [
            "Bachelor", "Master", "PhD", "Doctorate", "MBA", "BSc", "MSc", "BA", "MA",
            "University", "College", "School", "Institute", "Academy"
        ]
        
        education = []
        lines = content.split('\n')
        
        for line in lines:
            line_lower = line.lower()
            if any(keyword.lower() in line_lower for keyword in education_keywords):
                education.append({
                    "degree": line.strip(),
                    "raw_line": line.strip()
                })
        
        return education
    
    def analyze_cv_content(self, content: str, analysis_type: str = "comprehensive") -> Dict[str, Any]:
        """Comprehensive CV analysis."""
        contact_info = self.extract_contact_info(content)
        skills = self.extract_skills(content)
        experience = self.extract_experience(content)
        education = self.extract_education(content)
        
        # Calculate experience years (rough estimate)
        experience_years = 0
        for exp in experience:
            duration = exp.get("duration", "")
            if "Present" in duration or "Current" in duration:
                experience_years += 5  # Assume current role is ongoing
            else:
                # Try to extract years from duration
                years = re.findall(r'\d{4}', duration)
                if len(years) >= 2:
                    try:
                        start_year = int(years[0])
                        end_year = int(years[1])
                        experience_years += (end_year - start_year)
                    except ValueError:
                        pass
        
        analysis = {
            "contact_info": contact_info,
            "skills": skills,
            "experience": experience,
            "education": education,
            "estimated_experience_years": experience_years,
            "total_skills_count": len(skills),
            "total_experience_entries": len(experience),
            "total_education_entries": len(education)
        }
        
        if analysis_type == "skills_match":
            return self.analyze_skills_match(content, skills)
        elif analysis_type == "experience_summary":
            return self.analyze_experience_summary(content, experience, experience_years)
        else:
            return analysis
    
    def analyze_skills_match(self, content: str, skills: List[str], required_skills: List[str]) -> Dict[str, Any]:
        """Analyze skills match against required skills."""
        if not required_skills:
            # Default required skills for tech roles
            required_skills = ["Python", "JavaScript", "SQL", "Git"]
        
        matched_skills = [skill for skill in required_skills if skill in skills]
        match_percentage = (len(matched_skills) / len(required_skills)) * 100 if required_skills else 0
        
        return {
            "required_skills": required_skills,
            "candidate_skills": skills,
            "matched_skills": matched_skills,
            "missing_skills": [skill for skill in required_skills if skill not in skills],
            "match_percentage": match_percentage,
            "recommendation": self._get_skills_recommendation(match_percentage)
        }
    
    def analyze_experience_summary(self, content: str, experience: List[Dict[str, Any]], experience_years: int) -> Dict[str, Any]:
        """Analyze experience summary."""
        return {
            "total_experience_years": experience_years,
            "experience_entries": experience,
            "experience_summary": [exp.get("raw_line", "") for exp in experience],
            "key_achievements": self._extract_achievements(content),
            "seniority_level": self._determine_seniority_level(experience_years)
        }
    
    def _get_skills_recommendation(self, match_percentage: float) -> str:
        """Get recommendation based on skills match percentage."""
        if match_percentage >= 80:
            return "Excellent match - Strong candidate"
        elif match_percentage >= 60:
            return "Good match - Consider for role"
        elif match_percentage >= 40:
            return "Moderate match - May need training"
        else:
            return "Weak match - Not recommended"
    
    def _extract_achievements(self, content: str) -> List[str]:
        """Extract key achievements from CV content."""
        achievement_keywords = [
            "led", "managed", "developed", "implemented", "increased", "decreased",
            "improved", "created", "designed", "built", "launched", "delivered"
        ]
        
        achievements = []
        sentences = re.split(r'[.!?]', content)
        
        for sentence in sentences:
            sentence_lower = sentence.lower()
            if any(keyword in sentence_lower for keyword in achievement_keywords):
                if len(sentence.strip()) > 10:
                    achievements.append(sentence.strip())
        
        return achievements[:5]  # Return top 5 achievements
    
    def _determine_seniority_level(self, experience_years: int) -> str:
        """Determine seniority level based on experience years."""
        if experience_years >= 10:
            return "Senior/Lead"
        elif experience_years >= 5:
            return "Mid-level"
        elif experience_years >= 2:
            return "Junior"
        else:
            return "Entry-level"

def analyze_cv_content(doc_id: str, content: str, analysis_type: str, tool_context: ToolContext) -> Dict[str, Any]:
    """Tool function to analyze CV content."""
    analyzer = CVAnalyzer()
    
    try:
        if analysis_type == "comprehensive":
            analysis = analyzer.analyze_cv_content(content)
        elif analysis_type == "skills_match":
            analysis = analyzer.analyze_cv_content(content, "skills_match")
        elif analysis_type == "experience_summary":
            analysis = analyzer.analyze_cv_content(content, "experience_summary")
        else:
            analysis = analyzer.analyze_cv_content(content)
        
        return {
            "status": "success",
            "doc_id": doc_id,
            "analysis_type": analysis_type,
            "analysis": analysis
        }
        
    except Exception as e:
        return {
            "status": "error",
            "doc_id": doc_id,
            "error_message": f"Failed to analyze CV: {str(e)}"
        }

def analyze_cv_by_name(doc_name: str, folder_id: str, content: str, analysis_type: str, tool_context: ToolContext) -> Dict[str, Any]:
    """Tool function to analyze CV by name."""
    return analyze_cv_content(f"file_by_name_{doc_name}", content, analysis_type, tool_context)
