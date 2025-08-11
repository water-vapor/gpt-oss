"""Stateful Python execution tool (maintains state across calls)"""
import sys
import io
import ast
from typing import AsyncIterator

from openai_harmony import (
    Author,
    Message,
    Role,
    TextContent,
    ToolNamespaceConfig,
)

from .tool import Tool


class StatefulPythonExecutor:
    """REPL-like Python executor with persistent state across calls"""
    
    _RESULT_SLOT = "_REPL_LAST_EXPR_VALUE_DO_NOT_USE_"
    
    def __init__(self):
        # Single, persistent environment (like launching `python`)
        self.globals = {}
        self.globals["__name__"] = "__main__"
        self.globals["__package__"] = None
        self.globals["__builtins__"] = __builtins__
        self.execution_count = 0
    
    def execute(self, code: str) -> str:
        """Execute Python code in the persistent environment"""
        self.execution_count += 1
        
        if not code.strip():
            return f"[Execution #{self.execution_count}]"
        
        # Capture output
        stdout = io.StringIO()
        stderr = io.StringIO()
        
        # Redirect stdout and stderr
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = stdout
        sys.stderr = stderr
        
        result_value = None
        try:
            # Parse the code once
            try:
                module = ast.parse(code, filename="<input>", mode="exec")
            except SyntaxError:
                # Syntax error - will be caught and reported below
                import traceback
                stderr.write(traceback.format_exc())
            else:
                # If the last top-level statement is an expression, rewrite it to capture the value
                capture_result = False
                if module.body and isinstance(module.body[-1], ast.Expr):
                    last_expr = module.body[-1].value
                    # Rewrite to: _RESULT_SLOT = <expr>
                    assign = ast.Assign(
                        targets=[ast.Name(id=self._RESULT_SLOT, ctx=ast.Store())],
                        value=last_expr
                    )
                    ast.copy_location(assign, module.body[-1])
                    module.body[-1] = assign
                    capture_result = True
                
                ast.fix_missing_locations(module)
                
                # Compile the whole module once
                code_obj = compile(module, "<input>", "exec")
                
                # Execute in the persistent namespace
                exec(code_obj, self.globals, self.globals)
                
                # Get the captured result if there was an expression
                if capture_result and self._RESULT_SLOT in self.globals:
                    result_value = self.globals.pop(self._RESULT_SLOT)
                    self.globals["_"] = result_value  # Set _ like the REPL does
                    
        except Exception:
            # Runtime error
            import traceback
            stderr.write(traceback.format_exc())
        finally:
            # Restore stdout and stderr
            sys.stdout = old_stdout
            sys.stderr = old_stderr
        
        # Format output
        output_text = stdout.getvalue()
        error_text = stderr.getvalue()
        
        # Build result string
        parts = []
        if output_text:
            parts.append(output_text.rstrip())
        if result_value is not None:
            # Add the expression result (like REPL does)
            parts.append(repr(result_value))
        if error_text:
            parts.append(error_text.rstrip())
        
        if parts:
            return f"[Execution #{self.execution_count}]\n" + "\n".join(parts)
        else:
            return f"[Execution #{self.execution_count}]"


class StatefulPythonTool(Tool):
    """Stateful Python execution tool - maintains state across calls in a session"""
    
    def __init__(self):
        self.executor = StatefulPythonExecutor()
    
    @property
    def name(self) -> str:
        """Tool identifier"""
        return "python"
    
    def instruction(self) -> str:
        """Tool instructions"""
        return """Execute Python code with persistent state.
        
IMPORTANT: This Python environment maintains state across calls within this conversation.
- Variables, imports, and functions persist between executions
- Expression results are automatically displayed (REPL behavior)
- The last expression value is available as _ (underscore)
- You can reference variables from previous executions
- The environment resets when the conversation ends"""
    
    @property
    def tool_config(self) -> ToolNamespaceConfig:
        return ToolNamespaceConfig(
            name="python",
            description="Execute Python code with persistent state across calls",
            tools=[]
        )
    
    async def _process(self, message: Message) -> AsyncIterator[Message]:
        """Process a Python execution request"""
        # Extract the code from the message
        code = message.content[0].text if message.content else ""
        
        if not code:
            output = "Error: No code provided"
        else:
            # Execute the code
            output = self.executor.execute(code)
        
        # Create response message
        response = (
            Message(
                author=Author(role=Role.TOOL, name="python"),
                content=[TextContent(text=output)]
            )
            .with_recipient("assistant")
        )
        
        if message.channel:
            response = response.with_channel(message.channel)
        
        yield response